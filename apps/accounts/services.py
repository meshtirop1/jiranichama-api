"""Phone sign-in: request a code by SMS, verify it, get tokens. See docs/flows/phone-sign-in.md."""

import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.crypto import keyed_hash
from apps.core.problems import Problem
from apps.notify.sms import send_sms

from .models import OtpChallenge, User
from .phones import phone_hash

SMS_TEMPLATES = {
    "en": "JiraniChama code: {code}. It expires in 5 minutes. Never share this code.",
    "sw": "Nambari ya JiraniChama: {code}. Itaisha baada ya dakika 5. Usimpe mtu yeyote.",
}


def _code_hmac(challenge_id, code: str) -> str:
    return keyed_hash("otp", f"{challenge_id}:{code}")


def request_code(e164: str, language: str = "en") -> OtpChallenge:
    ph = phone_hash(e164)
    now = timezone.now()
    window_start = now - timedelta(seconds=settings.OTP_WINDOW_SECONDS)
    recent = OtpChallenge.objects.filter(phone_hash=ph, created_at__gte=window_start).count()
    if recent >= settings.OTP_MAX_PER_WINDOW:
        raise Problem(
            "otp_rate_limited",
            "Too many codes requested. Wait 10 minutes and try again.",
            status.HTTP_429_TOO_MANY_REQUESTS,
        )

    code = f"{secrets.randbelow(10**6):06d}"
    with transaction.atomic():
        challenge = OtpChallenge(phone_hash=ph, expires_at=now + timedelta(
            seconds=settings.OTP_TTL_SECONDS))
        challenge.code_hmac = _code_hmac(challenge.id, code)
        challenge.save()

    # Sent from the request, not a queue, so the code never passes through the broker.
    template = SMS_TEMPLATES.get(language, SMS_TEMPLATES["en"])
    send_sms(e164, template.format(code=code))
    return challenge


@dataclass
class SignInResult:
    user: User
    is_new: bool
    access: str
    refresh: str


def verify_code(e164: str, code: str) -> SignInResult:
    ph = phone_hash(e164)
    now = timezone.now()
    # A wrong guess must be committed before we refuse it; raising inside the atomic
    # block would roll the attempt counter back and allow unlimited guesses.
    wrong_attempts_left = None
    with transaction.atomic():
        challenge = (
            OtpChallenge.objects.select_for_update()
            .filter(phone_hash=ph, used_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        if challenge is None or challenge.expires_at <= now:
            raise Problem("otp_expired", "This code has expired. Ask for a new one.")
        if challenge.attempts >= settings.OTP_MAX_ATTEMPTS:
            raise Problem("otp_locked", "Too many wrong codes. Ask for a new one.")

        challenge.attempts += 1
        if not hmac.compare_digest(challenge.code_hmac, _code_hmac(challenge.id, code)):
            challenge.save(update_fields=["attempts"])
            wrong_attempts_left = settings.OTP_MAX_ATTEMPTS - challenge.attempts
    if wrong_attempts_left is not None:
        raise Problem("otp_wrong", "That code is not right.", attempts_left=wrong_attempts_left)

    with transaction.atomic():
        challenge = OtpChallenge.objects.select_for_update().get(pk=challenge.pk)
        if challenge.used_at is not None:
            raise Problem("otp_expired", "This code has expired. Ask for a new one.")
        challenge.attempts += 1
        challenge.used_at = now
        challenge.save(update_fields=["attempts", "used_at"])

        user = User.objects.filter(phone_hash=ph).first()
        is_new = user is None
        if is_new:
            user = User.objects.create_user(e164)
        elif not user.is_active:
            raise Problem("account_inactive", "This account is closed.",
                          status.HTTP_403_FORBIDDEN)

    refresh = RefreshToken.for_user(user)
    return SignInResult(user=user, is_new=is_new, access=str(refresh.access_token),
                        refresh=str(refresh))


def set_pin(user: User, pin: str, current_pin: str | None = None) -> None:
    if user.has_pin and not user.check_pin(current_pin or ""):
        raise Problem("pin_wrong", "Your current PIN is not right.",
                      status.HTTP_403_FORBIDDEN)
    user.set_pin(pin)
    user.save(update_fields=["pin_hash"])
