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
