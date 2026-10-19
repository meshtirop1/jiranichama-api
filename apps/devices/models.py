"""Signing: SMS codes for everyone, and device keys for officials with the app.

``SigningChallenge`` is a purpose-bound SMS code, the design copied from JiraniSoko
Motors. ``Device`` is a P-256 key in the phone's secure chip (6.7); officials who have one
approve money out with their fingerprint, face or phone PIN instead of an SMS code.
"""

import uuid

from django.conf import settings
from django.db import models


class SigningChallenge(models.Model):
    class Purpose(models.TextChoices):
        JOIN_CHAMA = "join_chama"
        ADOPT_RULES = "adopt_rules"
        CONFIRM_STATEMENT = "confirm_statement"
        ASSIGN_PAYMENT = "assign_payment"
        CONFIRM_OPENING = "confirm_opening"
        CONFIRM_CASH = "confirm_cash"
        APPROVE_PROPOSAL = "approve_proposal"
        ENABLE_MONEY_OUT = "enable_money_out"
        REGISTER_DEVICE = "register_device"
        CAST_BALLOT = "cast_ballot"
        GUARANTEE_LOAN = "guarantee_loan"
        APPROVE_WAIVER = "approve_waiver"
        DECIDE_DISPUTE = "decide_dispute"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name="+")
    purpose = models.CharField(max_length=32, choices=Purpose.choices)
    subject = models.CharField(max_length=200)  # e.g. "chamas.chama:<uuid>"
    content_hash = models.CharField(max_length=64)
    code_hmac = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "devices_signing_challenge"
        indexes = [models.Index(fields=["user", "purpose", "subject"])]


class Device(models.Model):
    """A phone's signing key: P-256, made in the phone's secure chip and usable only after
    the owner's fingerprint, face or phone PIN (6.7). We hold only the public key.

    Written once; only ``state`` and ``revoked_at`` change afterwards."""

    class State(models.TextChoices):
        ACTIVE = "active"
        REVOKED = "revoked"

    class Security(models.TextChoices):
        STRONGBOX = "strongbox"   # a separate secure chip
        TEE = "tee"               # the processor's trusted environment
        SOFTWARE = "software"     # no hardware protection (emulators)
        UNKNOWN = "unknown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name="devices")
    public_key_der = models.BinaryField()  # SubjectPublicKeyInfo
    fingerprint = models.CharField(max_length=64, unique=True)  # SHA-256 of the SPKI
    platform = models.CharField(max_length=10)
    label = models.CharField(max_length=80, blank=True, default="")
    security_level = models.CharField(max_length=10, choices=Security.choices,
                                      default=Security.UNKNOWN)
    attestation = models.JSONField(default=dict, blank=True)
    attestation_root_trusted = models.BooleanField(default=False)
    state = models.CharField(max_length=8, choices=State.choices, default=State.ACTIVE)
    registered_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    MUTABLE = ["state", "revoked_at"]

    class Meta:
        db_table = "devices_device"


class DeviceNonce(models.Model):
    """A single-use server nonce: put in a key's attestation when it is made, or in a
    payload a key signs, so neither can be replayed. Five minutes."""

    class Use(models.TextChoices):
        ATTEST = "attest"
        SIGN = "sign"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name="+")
    use = models.CharField(max_length=8, choices=Use.choices)
    subject = models.CharField(max_length=200, blank=True, default="")
    nonce = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "devices_nonce"
