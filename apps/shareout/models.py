"""Year-end share-out (owner request 2026-10-21).

At the end of a cycle many chamas (table banking, VSLAs; Uganda's law asks for it yearly)
give each member back their savings and a share of what the group earned: fines, loan
interest, bank interest and investment income, less bank charges. JiraniChama works the
figures out, the members vote on them, and each member's payment then goes through money
out like any other.
"""

import uuid

from django.db import models

from apps.chamas.models import Chama, Membership
from apps.ledger.models import LedgerEntry


class ShareOut(models.Model):
    """One share-out. The figures are frozen when it goes to the vote; only the state and
    what it led to change afterwards."""

    class State(models.TextChoices):
        DRAFT = "draft"          # officials look at the figures
        VOTING = "voting"        # the members vote on exactly these figures
        PAYING = "paying"        # passed; the payments are in money out
        DONE = "done"            # every payment matched in the bank
        REJECTED = "rejected"    # the vote failed or lapsed
        CANCELLED = "cancelled"  # an official dropped the draft

    OPEN = (State.DRAFT, State.VOTING, State.PAYING)

    class Weight(models.TextChoices):
        SAVINGS = "savings"                # by what each member has saved
        SAVINGS_MONTHS = "savings_months"  # by money saved times the months it was held

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="shareouts")
    label = models.CharField(max_length=40)  # "2026"
    period_from = models.DateField()
    period_to = models.DateField()
    return_savings = models.BooleanField(default=True)
    keep_pct = models.PositiveSmallIntegerField(default=0)  # kept back as a reserve
    weight = models.CharField(max_length=16, choices=Weight.choices,
                              default=Weight.SAVINGS_MONTHS)
    figures = models.JSONField(default=dict)
    figures_hash = models.CharField(max_length=64)
    created_by = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    state = models.CharField(max_length=10, choices=State.choices, default=State.DRAFT)
    vote = models.ForeignKey("votes.Vote", on_delete=models.PROTECT, null=True, blank=True,
                             related_name="+")
    entry = models.ForeignKey(LedgerEntry, on_delete=models.PROTECT, null=True, blank=True,
                              related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE = ["state", "vote_id", "entry_id"]

    class Meta:
        db_table = "shareout_shareout"
        indexes = [models.Index(fields=["chama", "state"])]


class ShareOutLine(models.Model):
    """One member's part, as voted. Only the payment proposal is filled in later."""

    id = models.BigAutoField(primary_key=True)
    shareout = models.ForeignKey(ShareOut, on_delete=models.PROTECT, related_name="lines")
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    savings_cents = models.BigIntegerField()      # returned (0 when savings stay)
    profit_cents = models.BigIntegerField()       # their share of what the group earned
    fines_cents = models.BigIntegerField()        # unpaid fines, paid from the share-out
    loan_cents = models.BigIntegerField()         # an open loan, paid from the share-out
    payout_cents = models.BigIntegerField()       # what the bank pays them
    proposal = models.OneToOneField("payouts.Proposal", on_delete=models.PROTECT, null=True,
                                    blank=True, related_name="shareout_line")
    entry = models.ForeignKey(LedgerEntry, on_delete=models.PROTECT, null=True, blank=True,
                              related_name="+")  # nothing to pay: settled in the books

    MUTABLE = ["proposal_id", "entry_id"]

    class Meta:
        db_table = "shareout_line"
        constraints = [models.UniqueConstraint(fields=["shareout", "membership"],
                                               name="one_line_per_member")]
