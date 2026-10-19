import uuid

from django.db import models

from apps.chamas.models import Chama, Membership
from apps.ledger.models import LedgerEntry


class Exit(models.Model):
    """A member leaving, or a member who died (5.13), and the settlement of their money."""

    class Kind(models.TextChoices):
        LEAVE = "leave"
        DEATH = "death"

    class State(models.TextChoices):
        NOTICE = "notice"        # leaving; the notice period runs
        SETTLING = "settling"    # the settlement is with the officials as money out
        SETTLED = "settled"
        CANCELLED = "cancelled"  # the member changed their mind during notice

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="exits")
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="exits")
    kind = models.CharField(max_length=5, choices=Kind.choices)
    recorded_by = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    exit_on = models.DateField()
    quote = models.JSONField(default=dict)  # the calculation shown, then the one settled
    state = models.CharField(max_length=10, choices=State.choices, default=State.NOTICE)
    proposal = models.ForeignKey("payouts.Proposal", on_delete=models.PROTECT, null=True,
                                 blank=True, related_name="+")
    benefit_proposal = models.ForeignKey("payouts.Proposal", on_delete=models.PROTECT,
                                         null=True, blank=True, related_name="+")
    # Death only.
    died_on = models.DateField(null=True, blank=True)
    next_of_kin_name = models.CharField(max_length=120, blank=True)
    next_of_kin_phone = models.CharField(max_length=20, blank=True)
    document = models.FileField(upload_to="exits/%Y/%m/", blank=True)
    document_sha256 = models.CharField(max_length=64, blank=True)
    entry = models.ForeignKey(LedgerEntry, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE = ["state", "quote", "proposal_id", "benefit_proposal_id"]

    class Meta:
        db_table = "exits_exit"
