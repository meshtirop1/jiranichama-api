"""Chamas, memberships, invites, bank accounts and statement inboxes (Part 5.1, 5.2)."""

import uuid

from django.conf import settings
from django.db import models

from apps.core import countries
from apps.core.crypto import decrypt, encrypt, keyed_hash

OFFICIAL_ROLES = ("chair", "treasurer", "secretary")


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

    class Meets(models.TextChoices):
        WEEKLY = "weekly"
        FORTNIGHTLY = "fortnightly"
        MONTHLY = "monthly"
        RARELY = "rarely"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=32, choices=Type.choices)
    state = models.CharField(max_length=32, choices=State.choices, default=State.SETUP)
    mode = models.CharField(max_length=32, choices=Mode.choices, default=Mode.RECORDS_ONLY)
    member_code_prefix = models.CharField(max_length=12, unique=True)
    next_member_no = models.PositiveIntegerField(default=1)
    expected_members = models.PositiveIntegerField(default=0)
    meets = models.CharField(max_length=16, choices=Meets.choices, default=Meets.MONTHLY)
    # Where the chama is: its law (rule pack), currency and time zone. Fixed at creation.
    country = models.CharField(max_length=2, default=countries.DEFAULT_COUNTRY)
    currency = models.CharField(max_length=3, default="KES")
    # Settings being drafted during setup. Frozen into a RuleVersion at adoption.
    draft_rules = models.JSONField(default=dict, blank=True)
    draft_template = models.CharField(max_length=40, blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    setup_expires_at = models.DateTimeField(null=True, blank=True)
    adoption_opened_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "chamas_chama"

    def __str__(self):
        return self.name

    @property
    def in_setup(self) -> bool:
        return self.state in (self.State.SETUP, self.State.AWAITING_OFFICIALS)


class Membership(models.Model):
    class Role(models.TextChoices):
        MEMBER = "member"
        CHAIR = "chair"
        TREASURER = "treasurer"
        SECRETARY = "secretary"
        AUDITOR = "auditor"
        SIGNATORY = "signatory"

    class State(models.TextChoices):
        ACTIVE = "active"
        PROBATION = "probation"
        SUSPENDED = "suspended"
        EXITING = "exiting"
        EXITED = "exited"
        DECEASED = "deceased"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name="memberships")
    member_code = models.CharField(max_length=20)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.MEMBER)
    state = models.CharField(max_length=24, choices=State.choices, default=State.ACTIVE)
    is_founding = models.BooleanField(default=False)
    joined_on = models.DateField(null=True, blank=True)  # set when the chama is active
    rules_adopted_at = models.DateTimeField(null=True, blank=True)
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

    @property
    def is_official(self) -> bool:
        return self.role in OFFICIAL_ROLES

    @property
    def is_current(self) -> bool:
        return self.state in (self.State.ACTIVE, self.State.PROBATION)


class Invite(models.Model):
    class State(models.TextChoices):
        PENDING = "pending"
        ACCEPTED = "accepted"
        DECLINED = "declined"
        EXPIRED = "expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="invites")
    code = models.CharField(max_length=6, unique=True)
    # When set, only this phone may accept (officials, and invites sent by SMS).
    phone_hash = models.CharField(max_length=80, blank=True, default="")
    phone_hint = models.CharField(max_length=24, blank=True, default="")
    role = models.CharField(max_length=16, choices=Membership.Role.choices,
                            default=Membership.Role.MEMBER)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name="+")
    state = models.CharField(max_length=16, choices=State.choices, default=State.PENDING)
    expires_at = models.DateTimeField()
    # Set when the invite went by SMS (5.2a), in this language: the person replies YES.
    sms_language = models.CharField(max_length=2, blank=True, default="")
    membership = models.ForeignKey(Membership, on_delete=models.PROTECT, null=True,
                                   blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chamas_invite"


class BankAccount(models.Model):
    class Bank(models.TextChoices):
        EQUITY = "equity", "Equity Bank"
        KCB = "kcb", "KCB Bank"
        COOP = "coop", "Co-operative Bank"
        NCBA = "ncba", "NCBA"
        ABSA = "absa", "Absa Bank"
        STANBIC = "stanbic", "Stanbic Bank"
        FAMILY = "family", "Family Bank"
        CENTENARY = "centenary", "Centenary Bank"
        DFCU = "dfcu", "dfcu Bank"
        CRDB = "crdb", "CRDB Bank"
        NMB = "nmb", "NMB Bank"
        NBC = "nbc", "NBC Bank"
        OTHER = "other", "Other bank"

    class StatementRoute(models.TextChoices):
        BANK_EMAIL = "bank_email", "The bank emails statements to the chama inbox"
        UPLOAD = "upload", "Officials upload statements"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="bank_accounts")
    bank = models.CharField(max_length=16, choices=Bank.choices)
    account_name = models.CharField(max_length=120)
    account_no_enc = models.BinaryField()
    account_no_hash = models.CharField(max_length=80)
    account_no_last4 = models.CharField(max_length=4)
    paybill = models.CharField(max_length=10, blank=True, default="")
    till = models.CharField(max_length=10, blank=True, default="")
    statement_route = models.CharField(max_length=16, choices=StatementRoute.choices,
                                       default=StatementRoute.UPLOAD)
    statement_password_enc = models.BinaryField(null=True, blank=True)
    active_from = models.DateTimeField(auto_now_add=True)
    active_to = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "chamas_bank_account"
        constraints = [
            models.UniqueConstraint(fields=["chama"], condition=models.Q(active_to__isnull=True),
                                    name="one_active_bank_account"),
        ]

    @staticmethod
    def normalise_account_no(raw: str) -> str:
        return "".join(ch for ch in raw if ch.isdigit())

    @staticmethod
    def hash_account_no(raw: str) -> str:
        return keyed_hash("bank_account", BankAccount.normalise_account_no(raw))

    def set_account_no(self, raw: str) -> None:
        digits = self.normalise_account_no(raw)
        self.account_no_enc = encrypt(digits)
        self.account_no_hash = self.hash_account_no(digits)
        self.account_no_last4 = digits[-4:]

    @property
    def account_no(self) -> str:
        return decrypt(self.account_no_enc)

    def matches(self, raw: str) -> bool:
        return bool(raw) and self.hash_account_no(raw) == self.account_no_hash


class StatementInbox(models.Model):
    """The chama's own address for bank e-statements (proposal v1.1, 4.2)."""

    chama = models.OneToOneField(Chama, on_delete=models.PROTECT, primary_key=True,
                                 related_name="statement_inbox")
    address = models.CharField(max_length=120, unique=True)
    active = models.BooleanField(default=True)

    class Meta:
        db_table = "chamas_statement_inbox"
