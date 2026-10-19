import uuid

from django.db import models

from apps.chamas.models import Chama, Membership


class Loan(models.Model):
    """A member's loan from the chama (5.8). Behind the "loans" flag until the advocate's
    lending opinion (decision 4). The ledger holds the money facts (loan_disbursed,
    loan_interest, loan_repayment, fine_raised for penalties); this row holds the terms
    agreed and where the loan stands."""

    class State(models.TextChoices):
        AWAITING_GUARANTORS = "awaiting_guarantors"
        AWAITING_APPROVAL = "awaiting_approval"  # a money-out proposal is collecting
        APPROVED = "approved"                    # waiting to be paid at the bank
        REPAYING = "repaying"
        IN_ARREARS = "in_arrears"
        DEFAULTED = "defaulted"
        REPAID = "repaid"
        DECLINED = "declined"
        EXPIRED = "expired"
        CANCELLED = "cancelled"

    OPEN = (State.AWAITING_GUARANTORS, State.AWAITING_APPROVAL, State.APPROVED,
            State.REPAYING, State.IN_ARREARS, State.DEFAULTED)
    RUNNING = (State.REPAYING, State.IN_ARREARS, State.DEFAULTED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="loans")
    borrower = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="loans")
    amount_cents = models.BigIntegerField()
    term_months = models.PositiveSmallIntegerField()
    purpose = models.CharField(max_length=200)
    rule_version_id = models.UUIDField()
    terms = models.JSONField()  # the loan rules as they were when requested
    # The schedule shown at request; the real one starts from the disbursement date.
    schedule = models.JSONField()
    state = models.CharField(max_length=20, choices=State.choices,
                             default=State.AWAITING_GUARANTORS)
    state_reason = models.CharField(max_length=300, blank=True)
    proposal = models.ForeignKey("payouts.Proposal", on_delete=models.PROTECT, null=True,
                                 blank=True, related_name="+")
    disbursed_on = models.DateField(null=True, blank=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE = ["state", "state_reason", "proposal_id", "disbursed_on", "schedule"]

    class Meta:
        db_table = "loans_loan"


class Guarantee(models.Model):
    class State(models.TextChoices):
        REQUESTED = "requested"
        ACCEPTED = "accepted"
        DECLINED = "declined"
        EXPIRED = "expired"       # no answer in 72 hours, or the loan ended first
        RELEASED = "released"     # the loan is repaid, declined or cancelled
        CALLED = "called"         # the loan defaulted

    HOLDING = (State.REQUESTED, State.ACCEPTED, State.CALLED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    loan = models.ForeignKey(Loan, on_delete=models.PROTECT, related_name="guarantees")
    guarantor = models.ForeignKey(Membership, on_delete=models.PROTECT,
                                  related_name="guarantees")
    share_cents = models.BigIntegerField()
    state = models.CharField(max_length=10, choices=State.choices, default=State.REQUESTED)
    challenge_id = models.UUIDField(null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE = ["state", "challenge_id", "answered_at"]

    class Meta:
        db_table = "loans_guarantee"


class LoanReminder(models.Model):
    """One SMS per instalment and kind (3 days before, on the day), so a re-run never
    texts twice."""

    id = models.BigAutoField(primary_key=True)
    loan = models.ForeignKey(Loan, on_delete=models.PROTECT, related_name="+")
    instalment = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=10)  # "before", "due", "default"
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "loans_reminder"
        constraints = [models.UniqueConstraint(fields=["loan", "instalment", "kind"],
                                               name="loan_reminder_once")]
