import uuid

from django.db import models

from apps.chamas.models import Chama, Membership
from apps.ledger.models import LedgerEntry


class Partner(models.Model):
    """A licensed partner a chama may be referred to (4.10): a bank or SACCO for group
    accounts and loans (CBK or SASRA licensed), or a broker for welfare cover (IRA
    licensed). Kept by staff; JiraniChama itself lends and insures nothing."""

    class Regulator(models.TextChoices):
        CBK = "cbk", "Central Bank of Kenya"
        SASRA = "sasra", "SACCO Societies Regulatory Authority"
        IRA = "ira", "Insurance Regulatory Authority"

    class Product(models.TextChoices):
        GROUP_ACCOUNT = "group_account"
        GROUP_LOAN = "group_loan"
        WELFARE_COVER = "welfare_cover"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    regulator = models.CharField(max_length=5, choices=Regulator.choices)
    licence_number = models.CharField(max_length=60)
    product = models.CharField(max_length=14, choices=Product.choices)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "referrals_partner"

    def __str__(self):
        return f"{self.name} ({self.get_product_display()})"


class Referral(models.Model):
    """A chama's consent to be introduced to one partner, given by a members' vote."""

    class State(models.TextChoices):
        VOTING = "voting"
        CONSENTED = "consented"   # staff may now pass the chama's details on
        REFUSED = "refused"
        WITHDRAWN = "withdrawn"   # the officials took the consent back before it was sent
        SENT = "sent"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="referrals")
    partner = models.ForeignKey(Partner, on_delete=models.PROTECT, related_name="referrals")
    proposed_by = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+")
    contact = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="+",
                                help_text="The official the partner may contact.")
    reason = models.CharField(max_length=300)
    vote_id = models.UUIDField(null=True, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.VOTING)
    consent_entry = models.ForeignKey(LedgerEntry, on_delete=models.PROTECT, null=True,
                                      blank=True, related_name="+")
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE = ["state", "vote_id", "consent_entry_id", "sent_at"]

    class Meta:
        db_table = "referrals_referral"
