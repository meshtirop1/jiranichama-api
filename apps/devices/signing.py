"""Purpose-bound SMS signing codes.

A code is tied to one person, one purpose, one subject and the exact content they were
shown (``content_hash``). The SMS names the action, so a code read out to a fraudster
says what it would approve: "…to JOIN Umoja Women Group and accept its rules."
Valid 5 minutes, 5 tries, one use.
"""

import hmac
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.core.crypto import keyed_hash
from apps.core.problems import Problem
from apps.notify.sms import send_sms

from .models import SigningChallenge

TTL = timedelta(minutes=5)
MAX_ATTEMPTS = 5
MAX_OPEN_PER_SUBJECT = 3  # codes asked for the same action within the TTL


def _code_hmac(challenge_id, code: str) -> str:
    return keyed_hash("signing", f"{challenge_id}:{code}")


def issue(*, user, purpose: str, subject: str, content_hash: str, action_text: str,
          language: str | None = None) -> SigningChallenge:
    """Send a code by SMS. ``action_text`` completes "…to {action_text}. Never share
    this code." in the user's language (callers pass text in that language)."""
    now = timezone.now()
    recent = SigningChallenge.objects.filter(user=user, purpose=purpose, subject=subject,
                                             created_at__gte=now - TTL).count()
    if recent >= MAX_OPEN_PER_SUBJECT:
        raise Problem("signing_rate_limited", "Too many codes asked for. Wait 5 minutes.",
                      status.HTTP_429_TOO_MANY_REQUESTS)
    code = f"{secrets.randbelow(10**6):06d}"
    challenge = SigningChallenge(user=user, purpose=purpose, subject=subject,
                                 content_hash=content_hash, expires_at=now + TTL)
    challenge.code_hmac = _code_hmac(challenge.id, code)
    challenge.save()
    lang = language or user.language
    if lang == "sw":
        text = f"JiraniChama: nambari {code} ni ya {action_text}. Usimpe mtu yeyote."
    else:
        text = f"JiraniChama: {code} is your code to {action_text}. Never share this code."
    send_sms(user.phone, text)
    return challenge


def confirm(*, user, challenge_id, code: str, purpose: str, subject: str,
            content_hash: str) -> SigningChallenge:
    """Check a code. A wrong try is committed before it is refused."""
    wrong_left = None
    with transaction.atomic():
        challenge = (SigningChallenge.objects.select_for_update()
                     .filter(pk=challenge_id, user=user).first())
        if challenge is None or challenge.purpose != purpose or challenge.subject != subject:
            raise Problem("signing_invalid", "This code is not for this action.")
        if challenge.used_at or challenge.expires_at <= timezone.now():
            raise Problem("signing_expired", "This code has expired. Ask for a new one.")
        if challenge.attempts >= MAX_ATTEMPTS:
            raise Problem("signing_locked", "Too many wrong codes. Ask for a new one.")
        if challenge.content_hash != content_hash:
            # What is being approved changed after the code was sent.
            raise Problem("signing_content_changed",
                          "What you are confirming has changed. Ask for a new code.",
                          status.HTTP_409_CONFLICT)
        challenge.attempts += 1
        if not hmac.compare_digest(challenge.code_hmac, _code_hmac(challenge.id, code or "")):
            challenge.save(update_fields=["attempts"])
            wrong_left = MAX_ATTEMPTS - challenge.attempts
        else:
            challenge.used_at = timezone.now()
            challenge.save(update_fields=["attempts", "used_at"])
    if wrong_left is not None:
        raise Problem("signing_wrong", "That code is not right.", attempts_left=wrong_left)
    return challenge
