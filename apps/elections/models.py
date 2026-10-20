import uuid

from django.db import models

from apps.chamas.models import Chama, Membership
from apps.ledger.models import LedgerEntry


class Election(models.Model):
    """A members' vote to put someone in an official role (5.12), or to confirm an acting
    official named after one left suddenly."""

    class State(models.TextChoices):
        VOTING = "voting"
        WON = "won"            # waiting for the new official's phone key
        IN_OFFICE = "in_office"
        ACTING = "acting"      # in the role already; the vote confirms within 30 days
        LOST = "lost"
        CANCELLED = "cancelled"
        AWAITING_LOT = "awaiting_lot"  # tied twice: the next public anchor decides

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="elections")
    role = models.CharField(max_length=10)
    candidate = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    outgoing = models.ForeignKey(Membership, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="+")
    proposed_by = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    acting = models.BooleanField(default=False)
    term_months = models.PositiveSmallIntegerField()
    vote_id = models.UUIDField(null=True, blank=True)
    state = models.CharField(max_length=16, choices=State.choices, default=State.VOTING)
    role_entry = models.ForeignKey(LedgerEntry, on_delete=models.PROTECT, null=True,
                                   blank=True, related_name="+")
    # A bank signatory changed: the officials confirm the bank's mandate is updated.
    mandate_needed = models.BooleanField(default=False)
    mandate_confirmed_by = models.ForeignKey(Membership, on_delete=models.PROTECT, null=True,
                                             blank=True, related_name="+")
    mandate_confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # A tied vote is held again once; a second tie is settled by a public lot drawn from the
    # next night's anchor, which nobody can know or change in advance (owner 2026-10-20).
    tie_round = models.PositiveSmallIntegerField(default=0)
    lot_after = models.DateTimeField(null=True, blank=True)

    MUTABLE = ["state", "vote_id", "role_entry_id", "mandate_needed", "mandate_confirmed_by_id",
               "mandate_confirmed_at", "tie_round", "lot_after"]

    class Meta:
        db_table = "elections_election"
