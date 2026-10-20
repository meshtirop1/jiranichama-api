import uuid

from django.conf import settings
from django.db import models


class IdentityCheck(models.Model):
    """One ID check of one person, through Smile ID (owner decision 2026-10-20: Smile ID,
    paid by the chama).

    We keep what we need and nothing more:
    - the result, and the name the ID registry holds (to match against the profile and
      mobile-money names);
    - a KEYED hash of country + ID type + ID number, so one ID can belong to one person
      only. Never the number itself, and never a photo.
    Written once; only the result fields move while the provider answers.
    """

    class Status(models.TextChoices):
        PENDING = "pending"
        VERIFIED = "verified"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name="identity_checks")
    provider = models.CharField(max_length=20, default="smile_id")
    country = models.CharField(max_length=2)
    id_type = models.CharField(max_length=40)
    id_hash = models.CharField(max_length=80, db_index=True)
    last4 = models.CharField(max_length=4)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    result_code = models.CharField(max_length=10, blank=True, default="")
    registry_name = models.CharField(max_length=160, blank=True, default="")
    name_matches = models.BooleanField(default=False)
    # The chama that pays for this check (rule 6.1 "The chama pays for members' ID checks").
    paid_by = models.ForeignKey("chamas.Chama", on_delete=models.PROTECT, null=True,
                                blank=True, related_name="+")
    price_usd_cents = models.PositiveIntegerField(default=0)
    job_id = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    MUTABLE = ["status", "result_code", "registry_name", "name_matches", "decided_at"]

    class Meta:
        db_table = "identity_check"
