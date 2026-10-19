from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    """Every staff action, chained like the ledger so a deleted or edited row is found.
    Append-only: the database refuses UPDATE and DELETE on this table."""

    seq = models.PositiveBigIntegerField(unique=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True,
                              blank=True, related_name="+")
    action = models.CharField(max_length=64)
    target = models.CharField(max_length=200, blank=True, default="")
    reason = models.TextField()
    case_ref = models.CharField(max_length=64)
    ip = models.GenericIPAddressField(null=True, blank=True)
    at = models.DateTimeField()
    prev_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64, unique=True)

    class Meta:
        db_table = "staff_audit_log"
        ordering = ["-seq"]

    def __str__(self):
        return f"#{self.seq} {self.action} {self.target}"


class AuditHead(models.Model):
    """Single row holding the latest audit seq and hash; locked on every write."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    seq = models.PositiveBigIntegerField(default=0)
    hash = models.CharField(max_length=64)

    class Meta:
        db_table = "staff_audit_head"


class SupportGrant(models.Model):
    """A chama lets JiraniChama support read some of its records for a while (5.16).
    Asked for by one official, confirmed by another; every member is told; it ends by
    itself or when any official revokes it. Staff can only read, never change."""

    class Area(models.TextChoices):
        STATEMENTS = "statements"
        RULES = "rules"
        LEDGER = "ledger"
        MEMBERS = "members"
        DISPUTES = "disputes"

    class State(models.TextChoices):
        REQUESTED = "requested"
        ACTIVE = "active"
        EXPIRED = "expired"
        REVOKED = "revoked"

    import uuid as _uuid

    id = models.UUIDField(primary_key=True, default=_uuid.uuid4, editable=False)
    chama = models.ForeignKey("chamas.Chama", on_delete=models.PROTECT,
                              related_name="support_grants")
    areas = models.JSONField()  # list of Area values
    hours = models.PositiveSmallIntegerField(default=72)
    reason = models.CharField(max_length=300)
    requested_by = models.ForeignKey("chamas.Membership", on_delete=models.PROTECT,
                                     related_name="+")
    confirmed_by = models.ForeignKey("chamas.Membership", on_delete=models.PROTECT, null=True,
                                     blank=True, related_name="+")
    revoked_by = models.ForeignKey("chamas.Membership", on_delete=models.PROTECT, null=True,
                                   blank=True, related_name="+")
    state = models.CharField(max_length=10, choices=State.choices, default=State.REQUESTED)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE = ["confirmed_by_id", "revoked_by_id", "state", "starts_at", "ends_at"]

    class Meta:
        db_table = "staff_support_grant"
