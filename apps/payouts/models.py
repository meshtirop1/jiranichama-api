"""Money out (5.6, 5.7): proposals approved by officials, the bank debit matched back to
them, and any debit that left the account without an approval."""

import uuid

from django.db import models

from apps.chamas.models import Chama, Membership
from apps.feeds.models import BankTransaction
from apps.ledger.models import LedgerEntry


class Proposal(models.Model):
    """A request to pay money out of the chama's bank account. JiraniChama decides whether
    it is allowed and records who approved it; the bank signatories move the money.

    Written once; only the state and the payment and matching fields change afterwards
    (the database enforces that).
    """

    class Kind(models.TextChoices):
        PAYOUT = "payout"          # to a member (a turn, welfare, a dividend)
        WITHDRAWAL = "withdrawal"  # to anyone else (a supplier, an asset)

    class State(models.TextChoices):
        COLLECTING = "collecting_approvals"
        APPROVED = "approved"
        PAID_PENDING_MATCH = "paid_pending_match"
        PAID = "paid"
        REFUSED = "refused"      # a rule refused it; nobody was asked to approve
        REJECTED = "rejected"    # declines made the threshold unreachable
        EXPIRED = "expired"      # approvals not in by the deadline
        CANCELLED = "cancelled"  # the proposer withdrew it before payment

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="proposals")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    amount_cents = models.BigIntegerField()
    payee_membership = models.ForeignKey(Membership, on_delete=models.PROTECT, null=True,
                                         blank=True, related_name="payouts_to")
    payee_name = models.CharField(max_length=120)
    payee_account = models.CharField(max_length=120, blank=True, default="")
    purpose = models.CharField(max_length=200)
    proposed_by = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    rule_version_id = models.UUIDField(null=True, blank=True)
    needs_officials = models.PositiveSmallIntegerField(default=0)
    needs_vote = models.BooleanField(default=False)
    refusal = models.JSONField(default=dict, blank=True)  # {code, rule, message}
    deadline_at = models.DateTimeField(null=True, blank=True)
    state = models.CharField(max_length=24, choices=State.choices)
    state_reason = models.CharField(max_length=300, blank=True, default="")
    bank_reference = models.CharField(max_length=100, blank=True, default="")
    paid_by = models.ForeignKey(Membership, on_delete=models.PROTECT, null=True, blank=True,
                                related_name="+")
    paid_at = models.DateTimeField(null=True, blank=True)
    matched_txn = models.OneToOneField(BankTransaction, on_delete=models.PROTECT, null=True,
                                       blank=True, related_name="proposal")
    entry = models.OneToOneField(LedgerEntry, on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE = ["state", "state_reason", "bank_reference", "paid_by_id", "paid_at",
               "matched_txn_id", "entry_id"]

    class Meta:
        db_table = "payouts_proposal"
        indexes = [models.Index(fields=["chama", "state"])]


class Approval(models.Model):
    """One official's answer to a proposal, confirmed with an SMS code (approve) or
    given with a reason (decline). Insert only; one per official per proposal."""

    class Decision(models.TextChoices):
        APPROVE = "approve"
        DECLINE = "decline"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    proposal = models.ForeignKey(Proposal, on_delete=models.PROTECT, related_name="approvals")
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    role = models.CharField(max_length=16)
    decision = models.CharField(max_length=8, choices=Decision.choices)
    reason = models.CharField(max_length=300, blank=True, default="")
    challenge_id = models.UUIDField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "payouts_approval"
        constraints = [models.UniqueConstraint(fields=["proposal", "membership"],
                                               name="one_answer_per_official")]


class UnapprovedDebit(models.Model):
    """Money that left the bank account with no approved proposal (5.7 step 3). Every
    member is alerted the same day. Officials link it to a proposal or explain it."""

    class State(models.TextChoices):
        OPEN = "open"
        LINKED = "linked"        # it was an approved proposal paid without "Paid" first
        EXPLAINED = "explained"  # an official explained it; every member sees why

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="unapproved_debits")
    bank_txn = models.OneToOneField(BankTransaction, on_delete=models.PROTECT,
                                    related_name="unapproved")
    entry = models.OneToOneField(LedgerEntry, on_delete=models.PROTECT, related_name="+")
    state = models.CharField(max_length=12, choices=State.choices, default=State.OPEN)
    explanation = models.CharField(max_length=500, blank=True, default="")
    resolved_by = models.ForeignKey(Membership, on_delete=models.PROTECT, null=True,
                                    blank=True, related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)
    linked_proposal = models.ForeignKey(Proposal, on_delete=models.PROTECT, null=True,
                                        blank=True, related_name="+")
    alerted = models.PositiveIntegerField(default=0)  # members sent an SMS
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE = ["state", "explanation", "resolved_by_id", "resolved_at", "linked_proposal_id",
               "alerted"]

    class Meta:
        db_table = "payouts_unapproved_debit"


class MoneyOutConsent(models.Model):
    """An official agreeing, by SMS code, to switch the chama to money-out mode. Two
    officials are needed. Insert only."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="+")
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "payouts_money_out_consent"
        constraints = [models.UniqueConstraint(fields=["chama", "membership"],
                                               name="one_consent_per_official")]
