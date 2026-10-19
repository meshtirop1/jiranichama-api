from django.conf import settings
from django.db import models

from apps.chamas.models import Membership
from apps.feeds.models import BankTransaction


class Match(models.Model):
    """Every decision about who a bank line belongs to, in order. Append-only: an
    assignment that turns out wrong is followed by a new ``not_me`` row, never edited."""

    class Method(models.TextChoices):
        MEMBER_CODE = "member_code"
        PHONE = "phone"
        TREASURER = "treasurer"
        NOT_ME = "not_me"
        CASH = "cash"

    bank_txn = models.ForeignKey(BankTransaction, on_delete=models.PROTECT,
                                 related_name="matches")
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, null=True,
                                   blank=True, related_name="+")
    method = models.CharField(max_length=16, choices=Method.choices)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   null=True, blank=True, related_name="+")
    reason = models.TextField(blank=True, default="")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "reconciliation_match"
        ordering = ["at", "id"]


class ReconciliationRun(models.Model):
    """One daily check (5.5): the bank's balance against what the records say it should
    be. Insert only, so the history of checks is itself a record."""

    id = models.BigAutoField(primary_key=True)
    chama = models.ForeignKey("chamas.Chama", on_delete=models.PROTECT,
                              related_name="reconciliations")
    on = models.DateField()
    statement = models.ForeignKey("feeds.Statement", on_delete=models.PROTECT,
                                  related_name="+")
    bank_cents = models.BigIntegerField()
    records_cents = models.BigIntegerField()
    gap_cents = models.BigIntegerField()
    unmatched_cents = models.BigIntegerField(default=0)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "reconciliation_run"
        indexes = [models.Index(fields=["chama", "-at"])]


class ReconciliationGap(models.Model):
    """The bank and the records differ. Every member sees it. It closes when entries
    explain the difference; open for more than 7 days it is a breach and every member is
    told by SMS. Only its state, amount and dates change."""

    class State(models.TextChoices):
        OPEN = "open"
        BREACH = "breach"
        EXPLAINED = "explained"

    id = models.BigAutoField(primary_key=True)
    chama = models.ForeignKey("chamas.Chama", on_delete=models.PROTECT,
                              related_name="reconciliation_gaps")
    opened_on = models.DateField()
    first_gap_cents = models.BigIntegerField()
    amount_cents = models.BigIntegerField()
    state = models.CharField(max_length=10, choices=State.choices, default=State.OPEN)
    breach_on = models.DateField(null=True, blank=True)
    explained_on = models.DateField(null=True, blank=True)
    alerted = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE = ["amount_cents", "state", "breach_on", "explained_on", "alerted"]

    class Meta:
        db_table = "reconciliation_gap"
