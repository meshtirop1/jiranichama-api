import hashlib
import uuid

from django.db import models

from apps.chamas.models import Chama
from apps.core.canonical import canonical_bytes


def rules_hash(rules: dict) -> str:
    return hashlib.sha256(canonical_bytes(rules)).hexdigest()


class RuleVersion(models.Model):
    """One adopted set of a chama's rules. Written once at adoption; only ``state`` may
    change afterwards (scheduled → in_force → superseded). The database enforces it."""

    class State(models.TextChoices):
        SCHEDULED = "scheduled"
        IN_FORCE = "in_force"
        SUPERSEDED = "superseded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="rule_versions")
    version_no = models.PositiveIntegerField()
    rules = models.JSONField()
    schema_version = models.PositiveSmallIntegerField()
    template = models.CharField(max_length=40)
    effective_from = models.DateField()
    hash = models.CharField(max_length=64)
    state = models.CharField(max_length=16, choices=State.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rules_rule_version"
        ordering = ["chama", "version_no"]
        constraints = [
            models.UniqueConstraint(fields=["chama", "version_no"], name="rule_version_unique"),
        ]

    def __str__(self):
        return f"{self.chama_id} v{self.version_no}"


def version_at(chama: Chama, on) -> RuleVersion | None:
    """The rules in force on a business date: the latest version starting on or before it."""
    return (RuleVersion.objects.filter(chama=chama, effective_from__lte=on)
            .exclude(state=RuleVersion.State.SCHEDULED)
            .order_by("-effective_from", "-version_no").first())
