"""Chamas and memberships: the minimum the Phase 0 ledger needs to point at.

Phase 1 adds invites, member-code allocation, rules, bank accounts and the state
transitions in Part 5.1 and 5.2 of the proposal.
"""

import uuid

from django.conf import settings
from django.db import models


class Chama(models.Model):
    class Type(models.TextChoices):
        MERRY_GO_ROUND = "merry_go_round", "Merry-go-round"
        TABLE_BANKING = "table_banking", "Table banking"
        WELFARE = "welfare", "Welfare"
        INVESTMENT_CLUB = "investment_club", "Investment club"

    class State(models.TextChoices):
        SETUP = "setup"
        AWAITING_OFFICIALS = "awaiting_officials"
        AWAITING_ADOPTION = "awaiting_adoption"
        ACTIVE = "active"
        SUSPENDED = "suspended"
        CLOSED = "closed"
        EXPIRED = "expired"

    class Mode(models.TextChoices):
        RECORDS_ONLY = "records_only", "Records only"
        MONEY_OUT = "money_out", "Money out"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=32, choices=Type.choices)
    state = models.CharField(max_length=32, choices=State.choices, default=State.SETUP)
    mode = models.CharField(max_length=32, choices=Mode.choices, default=Mode.RECORDS_ONLY)
    member_code_prefix = models.CharField(max_length=12, unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chamas_chama"

    def __str__(self):
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        MEMBER = "member"
        CHAIR = "chair"
        TREASURER = "treasurer"
        SECRETARY = "secretary"
        AUDITOR = "auditor"
        SIGNATORY = "signatory"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name="memberships")
    member_code = models.CharField(max_length=20)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.MEMBER)
    state = models.CharField(max_length=24, default="active")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chamas_membership"
        constraints = [
            models.UniqueConstraint(fields=["chama", "member_code"],
                                    name="membership_code_unique"),
            models.UniqueConstraint(fields=["chama", "user"],
                                    condition=~models.Q(state="exited"),
                                    name="membership_one_open_per_user"),
        ]

    def __str__(self):
        return self.member_code
