import uuid

from django.db import models

from apps.chamas.models import Chama, Membership
from apps.ledger.models import LedgerEntry


class Dispute(models.Model):
    """A member says something in the records is wrong (5.14). The records stay as they
    are; only a payout or loan still waiting for approval pauses. The outcome is written
    as entries, never by editing anything."""

    class Subject(models.TextChoices):
        ENTRY = "entry"          # a ledger entry, by its id
        FINE = "fine"
        PROPOSAL = "proposal"    # money out, including loans
        PAYMENT = "payment"      # a bank line (raised by "not me")
        MINUTES = "minutes"
        OTHER = "other"          # a rule outcome or a breach, described in words

    class Reason(models.TextChoices):
        WRONG_AMOUNT = "wrong_amount"
        NOT_MINE = "not_mine"
        WRONG_RULE = "wrong_rule"
        NOT_APPROVED = "not_approved"
        OTHER = "other"

    class State(models.TextChoices):
        AWAITING_RESPONSE = "awaiting_response"  # 72 hours for the people involved
        DECIDING = "deciding"                    # an outcome is proposed
        RESOLVED = "resolved"
        WITHDRAWN = "withdrawn"
        ESCALATED_OUTSIDE = "escalated_outside"  # mediation, Small Claims Court, police

    class Outcome(models.TextChoices):
        NO_CHANGE = "no_change"
        REVERSAL = "reversal"      # the disputed entry is reversed
        CORRECTION = "correction"  # an amount is put right for a member
        REFUND = "refund"          # money is to be paid back (proposed in money out)

    OPEN = (State.AWAITING_RESPONSE, State.DECIDING)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="disputes")
    # Empty when JiraniChama raised it by itself (a withdrawal nobody explained, 2026-10-21).
    raised_by = models.ForeignKey(Membership, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="+")
    automatic = models.BooleanField(default=False)
    subject_type = models.CharField(max_length=10, choices=Subject.choices)
    subject_id = models.UUIDField(null=True, blank=True)
    reason = models.CharField(max_length=14, choices=Reason.choices)
    text = models.TextField()
    officials_only = models.BooleanField(default=False)
    involved = models.JSONField(default=list)  # membership ids asked to respond
    respond_until = models.DateTimeField()
    state = models.CharField(max_length=18, choices=State.choices,
                             default=State.AWAITING_RESPONSE)
    # The outcome being decided, then the one decided.
    outcome = models.CharField(max_length=10, choices=Outcome.choices, blank=True)
    outcome_text = models.TextField(blank=True)
    outcome_member = models.ForeignKey(Membership, on_delete=models.PROTECT, null=True,
                                       blank=True, related_name="+")
    outcome_cents = models.BigIntegerField(default=0)
    proposed_by = models.ForeignKey(Membership, on_delete=models.PROTECT, null=True,
                                    blank=True, related_name="+")
    round = models.PositiveSmallIntegerField(default=0)  # outcomes proposed so far
    vote_id = models.UUIDField(null=True, blank=True)
    entry = models.ForeignKey(LedgerEntry, on_delete=models.PROTECT, null=True, blank=True,
                              related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE = ["state", "round", "outcome", "outcome_text", "outcome_member_id", "outcome_cents",
               "proposed_by_id", "vote_id", "entry_id"]

    class Meta:
        db_table = "disputes_dispute"


class DisputeMessage(models.Model):
    """A response or a comment; and, for officials, their agreement with the proposed
    outcome."""

    class Kind(models.TextChoices):
        RESPONSE = "response"
        AGREE = "agree"
        DISAGREE = "disagree"
        NOTE = "note"          # withdrawal or escalation reason, outcome proposal

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dispute = models.ForeignKey(Dispute, on_delete=models.PROTECT, related_name="messages")
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    text = models.TextField(blank=True)
    round = models.PositiveSmallIntegerField(default=0)  # which proposed outcome
    challenge_id = models.UUIDField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "disputes_message"
