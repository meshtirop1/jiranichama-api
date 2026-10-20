import uuid

from django.db import models


class Bill(models.Model):
    """JiraniChama's monthly bill to one chama (owner decisions 2026-10-20): about USD 0.10
    per ACTIVE member per month, in local money, the chama paying one bill. The first three
    months are free, and chamas of five members or fewer are free for good. ID checks the
    chama paid for are added.

    This is JiraniChama's fee, not the chama's money, so it is not on the chama's ledger.
    The treasurer pays it like any payment (a money-out proposal to JiraniChama), so the
    members see that payment in their records. Written once; only the payment moves.
    """

    class State(models.TextChoices):
        FREE = "free"
        OPEN = "open"
        PAID = "paid"
        WAIVED = "waived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey("chamas.Chama", on_delete=models.PROTECT, related_name="bills")
    period = models.CharField(max_length=7)  # "2026-10"
    currency = models.CharField(max_length=3)
    active_members = models.PositiveIntegerField()
    price_per_member_cents = models.PositiveIntegerField()
    members_cents = models.BigIntegerField()
    id_checks = models.PositiveIntegerField(default=0)
    id_checks_cents = models.BigIntegerField(default=0)
    total_cents = models.BigIntegerField()
    free_reason = models.CharField(max_length=10, blank=True, default="")  # trial / small
    state = models.CharField(max_length=8, choices=State.choices)
    due_on = models.DateField()
    paid_at = models.DateTimeField(null=True, blank=True)
    paid_reference = models.CharField(max_length=60, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE = ["state", "paid_at", "paid_reference"]

    class Meta:
        db_table = "billing_bill"
        constraints = [models.UniqueConstraint(fields=["chama", "period"],
                                               name="one_bill_per_chama_per_month")]
