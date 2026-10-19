import uuid

from django.db import models

from apps.chamas.models import Chama, Membership
from apps.ledger.models import LedgerEntry


class Fine(models.Model):
    """A fine raised by a rule. The ledger holds the facts (fine_raised, fine_paid,
    reversal); this row is the quick view of where the fine stands.

    One fine per member, rule and period: the unique key makes the hourly job safe to run
    twice (5.9)."""

    class State(models.TextChoices):
        RAISED = "raised"
        PAID = "paid"
        REVERSED = "reversed"
        WAIVED = "waived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="fines")
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="fines")
    rule_ref = models.CharField(max_length=16)
    period = models.CharField(max_length=7)
    amount_cents = models.BigIntegerField()
    paid_cents = models.BigIntegerField(default=0)
    state = models.CharField(max_length=16, choices=State.choices, default=State.RAISED)
    raised_entry = models.ForeignKey(LedgerEntry, on_delete=models.PROTECT, related_name="+")
    meeting = models.ForeignKey("meetings.Meeting", on_delete=models.PROTECT, null=True,
                                blank=True, related_name="fines")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fines_fine"
        constraints = [
            models.UniqueConstraint(fields=["membership", "rule_ref", "period"],
                                    condition=models.Q(meeting__isnull=True),
                                    name="fine_once_per_rule_period"),
            models.UniqueConstraint(fields=["membership", "rule_ref", "meeting"],
                                    condition=models.Q(meeting__isnull=False),
                                    name="fine_once_per_rule_meeting"),
        ]

    @property
    def outstanding_cents(self) -> int:
        if self.state in (self.State.REVERSED, self.State.WAIVED):
            return 0
        return max(self.amount_cents - self.paid_cents, 0)
