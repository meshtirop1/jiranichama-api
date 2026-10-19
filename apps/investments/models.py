import uuid

from django.db import models

from apps.chamas.models import Chama, Membership
from apps.ledger.models import LedgerEntry


class Investment(models.Model):
    """Something the chama owns besides money in the bank (4.10): land, shares, a money
    market fund. Bought through money out, or recorded as already owned; valued from time
    to time; its income and sale come in through the bank like any money."""

    class Kind(models.TextChoices):
        LAND = "land"
        SHARES = "shares"
        MMF = "mmf"            # money market fund
        BOND = "bond"
        BUSINESS = "business"
        OTHER = "other"

    class State(models.TextChoices):
        HELD = "held"
        SOLD = "sold"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="investments")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    name = models.CharField(max_length=120)
    details = models.TextField(blank=True)
    bought_on = models.DateField()
    cost_cents = models.BigIntegerField()
    purchase = models.ForeignKey("payouts.Proposal", on_delete=models.PROTECT, null=True,
                                 blank=True, related_name="+")
    recorded_by = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    entry = models.ForeignKey(LedgerEntry, on_delete=models.PROTECT, related_name="+")
    state = models.CharField(max_length=4, choices=State.choices, default=State.HELD)
    sold_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE = ["state", "sold_on"]

    class Meta:
        db_table = "investments_investment"


class Valuation(models.Model):
    """What an investment is worth on a date, with its source. Recorded by one official,
    confirmed by another before it counts (the same two-person rule as statements)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    investment = models.ForeignKey(Investment, on_delete=models.PROTECT,
                                   related_name="valuations")
    value_cents = models.BigIntegerField()
    as_of = models.DateField()
    source = models.CharField(max_length=200)
    document = models.FileField(upload_to="valuations/%Y/%m/", blank=True)
    document_sha256 = models.CharField(max_length=64, blank=True)
    recorded_by = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    confirmed_by = models.ForeignKey(Membership, on_delete=models.PROTECT, null=True,
                                     blank=True, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    entry = models.ForeignKey(LedgerEntry, on_delete=models.PROTECT, null=True, blank=True,
                              related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE = ["confirmed_by_id", "confirmed_at", "entry_id"]

    class Meta:
        db_table = "investments_valuation"
