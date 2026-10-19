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


class RuleProposal(models.Model):
    """A proposed change to the rules (5.10): notice, then a members' vote, then a new
    version from the 1st of next month. Only its state and links change afterwards."""

    class State(models.TextChoices):
        NOTICE = "notice"        # members read it; comments come with meetings
        VOTING = "voting"
        ADOPTED = "adopted"      # a version is scheduled
        IN_FORCE = "in_force"    # its start date has come
        REJECTED = "rejected"
        LAPSED = "lapsed"
        WITHDRAWN = "withdrawn"

    OPEN = (State.NOTICE, State.VOTING)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="rule_proposals")
    proposed_by = models.ForeignKey("chamas.Membership", on_delete=models.PROTECT,
                                    related_name="+")
    base_version = models.ForeignKey(RuleVersion, on_delete=models.PROTECT, related_name="+")
    rules = models.JSONField()
    reason = models.CharField(max_length=500)
    changed = models.JSONField(default=list)  # ["contributions/amount_cents", ...]
    quorum_pct = models.PositiveSmallIntegerField()
    pass_pct = models.PositiveSmallIntegerField()
    notice_until = models.DateTimeField()
    vote_days = models.PositiveSmallIntegerField(default=7)
    state = models.CharField(max_length=10, choices=State.choices, default=State.NOTICE)
    vote_id = models.UUIDField(null=True, blank=True)
    new_version = models.ForeignKey(RuleVersion, on_delete=models.PROTECT, null=True,
                                    blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE = ["state", "vote_id", "new_version_id"]

    class Meta:
        db_table = "rules_rule_proposal"
