"""Opening balances (5.1 step 10) and cash paid at meetings (5.4)."""

import uuid

from django.conf import settings
from django.db import models

from apps.chamas.models import Chama, Membership
from apps.ledger.models import LedgerEntry


class OpeningBalance(models.Model):
    """What each member already had when a chama moves from a notebook. Drafted by the
    treasurer, confirmed by a different official with an SMS code, then written to the
    ledger. Members have 14 days to object to their own line."""

    class State(models.TextChoices):
        DRAFT = "draft"
        CONFIRMED = "confirmed"

    chama = models.OneToOneField(Chama, on_delete=models.PROTECT, primary_key=True,
                                 related_name="opening_balance")
    state = models.CharField(max_length=16, choices=State.choices, default=State.DRAFT)
    proposed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    related_name="+")
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                     null=True, blank=True, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    objection_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "contributions_opening_balance"


class OpeningBalanceLine(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    opening = models.ForeignKey(OpeningBalance, on_delete=models.PROTECT, related_name="lines")
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    savings_cents = models.BigIntegerField(default=0)
    fines_cents = models.BigIntegerField(default=0)
    savings_entry = models.ForeignKey(LedgerEntry, on_delete=models.PROTECT, null=True,
                                      blank=True, related_name="+")
    fines_entry = models.ForeignKey(LedgerEntry, on_delete=models.PROTECT, null=True,
                                    blank=True, related_name="+")

    class Meta:
        db_table = "contributions_opening_balance_line"
        constraints = [
            models.UniqueConstraint(fields=["opening", "membership"],
                                    name="opening_line_once_per_member"),
            models.CheckConstraint(condition=models.Q(savings_cents__gte=0, fines_cents__gte=0),
                                   name="opening_line_not_negative"),
        ]


class OpeningObjection(models.Model):
    """A member says their opening line is wrong. Nothing changes on the ledger; the
    objection is visible to every member, and Phase 3 turns it into a dispute."""

    line = models.ForeignKey(OpeningBalanceLine, on_delete=models.PROTECT,
                             related_name="objections")
    raised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                  related_name="+")
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "contributions_opening_objection"
        constraints = [
            models.UniqueConstraint(fields=["line", "raised_by"], name="one_objection_per_line"),
        ]


class CashReceipt(models.Model):
    """Cash handed to the treasurer at a meeting (5.4). It counts only once the member
    has confirmed it and the deposit shows up in the bank."""

    class State(models.TextChoices):
        RECORDED = "recorded"  # waiting for the member to confirm
        MEMBER_CONFIRMED = "member_confirmed"  # waiting for the bank deposit
        BANKED = "banked"
        DECLINED = "declined"  # the member says they did not hand over this cash

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="cash_receipts")
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT,
                                   related_name="cash_receipts")
    amount_cents = models.BigIntegerField()
    received_on = models.DateField()
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    related_name="+")
    state = models.CharField(max_length=24, choices=State.choices, default=State.RECORDED)
    member_confirmed_at = models.DateTimeField(null=True, blank=True)
    declined_reason = models.TextField(blank=True, default="")
    pending_entry = models.ForeignKey(LedgerEntry, on_delete=models.PROTECT, null=True,
                                      blank=True, related_name="+")
    banked_entry = models.ForeignKey(LedgerEntry, on_delete=models.PROTECT, null=True,
                                     blank=True, related_name="+")
    bank_txn = models.ForeignKey("feeds.BankTransaction", on_delete=models.PROTECT, null=True,
                                 blank=True, related_name="cash_receipts")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "contributions_cash_receipt"
        constraints = [
            models.CheckConstraint(condition=models.Q(amount_cents__gt=0),
                                   name="cash_receipt_positive"),
        ]
