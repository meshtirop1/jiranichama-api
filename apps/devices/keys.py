"""Registering a phone's signing key, and checking what it signs (6.7).
See docs/flows/device-keys.md."""

import base64
import hashlib
import secrets
from datetime import timedelta

from cryptography.hazmat.primitives import serialization
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.core.problems import Problem

from . import attestation, signing
from .models import Device, DeviceNonce, SigningChallenge

NONCE_TTL = timedelta(minutes=5)


def issue_nonce(user, use: str, subject: str = "") -> DeviceNonce:
    return DeviceNonce.objects.create(user=user, use=use, subject=subject,
                                      nonce=secrets.token_hex(16),
                                      expires_at=timezone.now() + NONCE_TTL)


def take_nonce(user, use: str, nonce: str, subject: str = "") -> DeviceNonce:
    """Use a nonce once. Refused if it is someone else's, for something else, old or used."""
    with transaction.atomic():
        n = DeviceNonce.objects.select_for_update().filter(nonce=nonce, user=user, use=use,
                                                           subject=subject).first()
        if n is None or n.used_at or n.expires_at <= timezone.now():
            raise Problem("nonce_invalid", "This request has expired. Try again.",
                          status.HTTP_409_CONFLICT)
        n.used_at = timezone.now()
        n.save(update_fields=["used_at"])
    return n


def _public_key(raw_b64: str) -> bytes:
    try:
        der = base64.b64decode(raw_b64)
        key = serialization.load_der_public_key(der)
    except (ValueError, TypeError) as exc:
        raise Problem("key_invalid", "The phone's key could not be read.") from exc
    from cryptography.hazmat.primitives.asymmetric import ec

    if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
        raise Problem("key_invalid", "The phone's key must be P-256.")
    return der


def _register_hash(der: bytes) -> str:
    return hashlib.sha256(b"register_device:" + der).hexdigest()


def start_register(user, *, public_key_b64: str) -> SigningChallenge:
    der = _public_key(public_key_b64)
    if Device.objects.filter(fingerprint=attestation.spki_fingerprint(der)).exists():
        raise Problem("key_registered", "This key is already registered.",
                      status.HTTP_409_CONFLICT)
    action = ("SET UP this phone to approve payments with your fingerprint"
              if user.language != "sw" else
              "KUWEKA simu hii kuidhinisha malipo kwa alama ya kidole")
    return signing.issue(user=user, purpose=SigningChallenge.Purpose.REGISTER_DEVICE,
                         subject=f"accounts.user:{user.id}", content_hash=_register_hash(der),
                         action_text=action)


def register(user, *, public_key_b64: str, platform: str, label: str, chain_b64: list[str],
             attest_nonce: str, challenge_id, sms_code: str,
             handover: dict | None = None) -> Device:
    """The SMS code proves the account owner is holding this phone; the attestation says
    where the key lives."""
    der = _public_key(public_key_b64)
    signing.confirm(user=user, challenge_id=challenge_id, code=sms_code,
                    purpose=SigningChallenge.Purpose.REGISTER_DEVICE,
                    subject=f"accounts.user:{user.id}", content_hash=_register_hash(der))
    if platform not in ("android", "ios"):
        raise Problem("platform_invalid", "Unknown phone platform.")
    take_nonce(user, DeviceNonce.Use.ATTEST, attest_nonce)
    result = attestation.Attestation()
    if platform == "android":
        try:
            result = attestation.check_android(chain_b64, der)
        except attestation.AttestationError as exc:
            raise Problem("attestation_invalid", f"The phone's key could not be vouched "
                                                 f"for: {exc}.") from exc
        if result.challenge != attest_nonce.encode():
            raise Problem("attestation_invalid", "The key was not made for this request.")
    if settings.DEVICE_REQUIRE_TRUSTED_ATTESTATION and not result.root_trusted:
        raise Problem("attestation_untrusted", "This phone cannot keep a signing key safely. "
                                               "Approve by SMS code instead.",
                      status.HTTP_403_FORBIDDEN)
    from . import changes

    old = changes.check_handover(user, der, handover) if handover else None
    with transaction.atomic():
        device = Device.objects.create(
            user=user, public_key_der=der, fingerprint=attestation.spki_fingerprint(der),
            platform=platform, label=(label or "")[:80],
            security_level=result.security_level, attestation=result.as_dict(),
            attestation_root_trusted=result.root_trusted, **changes.arrival_for(user, old))
        changes.after_create(device)
        if device.state == Device.State.ACTIVE:
            _announce(device)
    return device


def _announce(device: Device) -> None:
    """Every chama where this person is an official sees that they now sign with a phone."""
    from apps.chamas.models import OFFICIAL_ROLES, Membership
    from apps.ledger import services as ledger

    for m in Membership.objects.filter(user=device.user, role__in=OFFICIAL_ROLES,
                                       state=Membership.State.ACTIVE).select_related("chama"):
        ledger.append(chama=m.chama, type="device_registered", source="device",
                      source_id=device.id, member=m,
                      decision={"fingerprint": device.fingerprint, "platform": device.platform,
                                "security_level": device.security_level,
                                "attestation_trusted": device.attestation_root_trusted})


def active_device(user) -> Device | None:
    from .changes import promote_due

    promote_due(user)
    return (Device.objects.filter(user=user, state=Device.State.ACTIVE)
            .order_by("-registered_at").first())


def revoke(device: Device, user) -> Device:
    if device.user_id != user.id:
        raise Problem("not_found", "Device not found.", status.HTTP_404_NOT_FOUND)
    if device.state == Device.State.ACTIVE:
        device.state, device.revoked_at = Device.State.REVOKED, timezone.now()
        device.save(update_fields=["state", "revoked_at"])
    return device
