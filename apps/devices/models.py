"""Signing for people without a device key.

Phase 1 has ``SigningChallenge`` only: a purpose-bound SMS code, the design copied from
JiraniSoko Motors. Device keys (``Device``, P-256 in the phone's secure chip) arrive in
Phase 2 for approvals of money going out.
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
