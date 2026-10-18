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
