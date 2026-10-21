import uuid

from django.conf import settings
from django.db import models

from apps.chamas.models import BankAccount, Chama
from apps.core.crypto import decrypt, encrypt


class Statement(models.Model):
    """One bank statement, from the bank's email or an official's upload (5.5).

    Written once with everything parsed from it. Only ``state``, ``confirmed_by`` and
    ``confirmed_at`` may change afterwards, plus the period once unreadable lines are
    typed in (they may extend it); the database enforces that.
    """

    class Route(models.TextChoices):
        BANK_EMAIL = "bank_email"
        UPLOAD = "upload"

    class State(models.TextChoices):
        AWAITING_CONFIRMATION = "awaiting_confirmation"
        ACCEPTED = "accepted"
        REJECTED = "rejected"
        NEEDS_PARSER = "needs_parser"
        NEEDS_TYPING = "needs_typing"  # some lines could not be read; an official types them
        HELD_MAIL_CHECK = "held_mail_check"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="statements")
    bank_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT,
                                     related_name="statements")
    route = models.CharField(max_length=16, choices=Route.choices)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    null=True, blank=True, related_name="+")
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                     null=True, blank=True, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    file = models.FileField(upload_to="statements/%Y/%m/")
    filename = models.CharField(max_length=200)
    sha256 = models.CharField(max_length=64)
    mail_auth = models.JSONField(default=dict, blank=True)
    parser = models.CharField(max_length=40, blank=True, default="")
    period_from = models.DateField(null=True, blank=True)
    period_to = models.DateField(null=True, blank=True)
    opening_cents = models.BigIntegerField(null=True, blank=True)
    closing_cents = models.BigIntegerField(null=True, blank=True)
    parsed_lines = models.JSONField(default=list, blank=True)
    unreadable_lines = models.JSONField(default=list, blank=True)  # [{line_no, cells}]
    newest_first = models.BooleanField(default=False)  # file order, for typed-in lines
    problems = models.JSONField(default=list, blank=True)  # refuse acceptance
    flags = models.JSONField(default=list, blank=True)  # shown to all members, do not refuse
    state = models.CharField(max_length=24, choices=State.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "feeds_statement"
        constraints = [
            models.UniqueConstraint(fields=["chama", "sha256"], name="statement_file_once"),
        ]


class BankTransaction(models.Model):
    """One line from the bank, from any feed. Written once; only ``state`` may change."""

    class Source(models.TextChoices):
        STATEMENT = "statement"
        DARAJA = "daraja"
        BANK_API = "bank_api"

    class Direction(models.TextChoices):
        CREDIT = "credit"
        DEBIT = "debit"

    class State(models.TextChoices):
        UNMATCHED = "unmatched"
        RECORDED = "recorded"
        DUPLICATE = "duplicate"
        DEBIT_PENDING = "debit_pending"  # records-only chamas: debits wait here
        UNAPPROVED = "unapproved"  # money out with no approved proposal (payouts app)
        DISPUTED = "disputed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="bank_transactions")
    bank_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT,
                                     related_name="transactions")
    source = models.CharField(max_length=16, choices=Source.choices)
    external_ref = models.CharField(max_length=100)
    direction = models.CharField(max_length=8, choices=Direction.choices)
    amount_cents = models.BigIntegerField()
    value_date = models.DateField()
    narration_enc = models.BinaryField()
    payer_phone_hash = models.CharField(max_length=80, blank=True, default="")
    balance_cents = models.BigIntegerField(null=True, blank=True)
    statement = models.ForeignKey(Statement, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="transactions")
    duplicate_of = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="+")
    state = models.CharField(max_length=16, choices=State.choices)
    # Set when an official typed this line in because the file could not be read there.
    # Every member sees that it was typed, not read from the bank's file.
    typed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                 null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "feeds_bank_transaction"
        constraints = [
            models.UniqueConstraint(fields=["bank_account", "source", "external_ref"],
                                    name="bank_txn_once_per_source"),
        ]
        indexes = [models.Index(fields=["chama", "state"])]

    @property
    def narration(self) -> str:
        return decrypt(self.narration_enc) if self.narration_enc else ""

    def set_narration(self, text: str) -> None:
        self.narration_enc = encrypt(text or "")


class TypedLine(models.Model):
    """A statement line an official typed in because the file could not be read there.
    Written once, all of a statement's typed lines together, and only after they make
    the statement add up. Insert only."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    statement = models.ForeignKey(Statement, on_delete=models.PROTECT,
                                  related_name="typed_lines")
    line_no = models.PositiveIntegerField()
    value_date = models.DateField()
    narration = models.CharField(max_length=200, blank=True, default="")
    reference = models.CharField(max_length=100)
    credit_cents = models.BigIntegerField(default=0)
    debit_cents = models.BigIntegerField(default=0)
    balance_cents = models.BigIntegerField(null=True, blank=True)
    typed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                 related_name="+")
    typed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "feeds_typed_line"
        constraints = [
            models.UniqueConstraint(fields=["statement", "line_no"], name="typed_line_once"),
        ]


def _channel_token() -> str:
    import secrets

    return secrets.token_urlsafe(24)


class DarajaChannel(models.Model):
    """A chama's own M-Pesa paybill or till, sending us Daraja C2B notifications. Set up
    by staff once the chama has authorised it; the secret token is in our callback URLs."""

    class Kind(models.TextChoices):
        PAYBILL = "paybill"
        TILL = "till"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="daraja_channels")
    shortcode = models.CharField(max_length=10, unique=True)
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.PAYBILL)
    token = models.CharField(max_length=64, unique=True, default=_channel_token,
                             editable=False)
    active = models.BooleanField(default=True)
    registered_at = models.DateTimeField(null=True, blank=True)
    # The Lipa na M-Pesa Online passkey Safaricom gives the shortcode's owner, stored
    # encrypted. With it members can tap "Pay now" and get the M-Pesa PIN prompt
    # (2026-10-21). Without it they pay the usual way.
    stk_passkey_enc = models.BinaryField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "feeds_daraja_channel"

    def __str__(self):
        return f"{self.kind} {self.shortcode} ({self.chama})"

    @property
    def can_prompt(self) -> bool:
        return bool(self.active and self.stk_passkey_enc)

    def set_stk_passkey(self, raw: str) -> None:
        self.stk_passkey_enc = encrypt(raw.strip())

    def stk_passkey(self) -> str:
        return decrypt(bytes(self.stk_passkey_enc)) if self.stk_passkey_enc else ""


class StkRequest(models.Model):
    """A "Pay now" prompt sent to a member's phone (Lipa na M-Pesa Online). The money goes
    from the member's M-Pesa straight to the chama's own paybill or till; we only ask
    Safaricom to show the prompt. When Safaricom reports it paid, the payment is recorded
    like any M-Pesa payment, for the member who asked. Only ``state`` and the result
    fields change."""

    class State(models.TextChoices):
        SENT = "sent"          # the prompt is on the member's phone
        PAID = "paid"
        FAILED = "failed"      # cancelled, wrong PIN, not enough money, timed out
        REFUSED = "refused"    # Safaricom did not send the prompt

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="+")
    channel = models.ForeignKey(DarajaChannel, on_delete=models.PROTECT, related_name="+")
    membership = models.ForeignKey("chamas.Membership", on_delete=models.PROTECT,
                                   related_name="+")
    amount_cents = models.BigIntegerField()
    phone_hint = models.CharField(max_length=24)
    checkout_request_id = models.CharField(max_length=80, blank=True, default="",
                                           db_index=True)
    state = models.CharField(max_length=8, choices=State.choices, default=State.SENT)
    result_code = models.CharField(max_length=12, blank=True, default="")
    result_text = models.CharField(max_length=200, blank=True, default="")
    receipt = models.CharField(max_length=20, blank=True, default="")
    bank_txn = models.ForeignKey(BankTransaction, on_delete=models.PROTECT, null=True,
                                 blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    MUTABLE = ["state", "checkout_request_id", "result_code", "result_text", "receipt",
               "bank_txn_id"]

    class Meta:
        db_table = "feeds_stk_request"


class BankFeed(models.Model):
    """A chama's bank account read through the bank's own API (Phase 4): Equity's Jenga
    first. Read-only scopes only; we never ask for payment permissions. The credentials
    are stored encrypted and set by staff once the chama has authorised it."""

    class Provider(models.TextChoices):
        JENGA = "jenga", "Equity Jenga"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="bank_feeds")
    bank_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT, related_name="+")
    provider = models.CharField(max_length=10, choices=Provider.choices,
                                default=Provider.JENGA)
    account_number = models.CharField(max_length=40)
    country_code = models.CharField(max_length=2, default="KE")
    credentials_enc = models.BinaryField(default=b"", blank=True)
    start_date = models.DateField(help_text="Lines from this date are read.")
    active = models.BooleanField(default=True)
    # Worked out on the first pull for a chama with no statement yet: the balance the
    # records start from.
    opening_cents = models.BigIntegerField(null=True, blank=True)
    last_balance_cents = models.BigIntegerField(null=True, blank=True)
    balance_at = models.DateTimeField(null=True, blank=True)
    last_pulled_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "feeds_bank_feed"

    def __str__(self):
        return f"{self.get_provider_display()} {self.account_number[-4:]} ({self.chama})"

    def set_credentials(self, creds: dict) -> None:
        import json

        self.credentials_enc = encrypt(json.dumps(creds))

    def credentials(self) -> dict:
        import json

        return json.loads(decrypt(self.credentials_enc)) if self.credentials_enc else {}


class BankFeedPull(models.Model):
    """One read of a bank feed: the balance seen and how many new lines. Insert only."""

    id = models.BigAutoField(primary_key=True)
    feed = models.ForeignKey(BankFeed, on_delete=models.PROTECT, related_name="pulls")
    balance_cents = models.BigIntegerField(null=True, blank=True)
    lines = models.PositiveIntegerField(default=0)
    new_lines = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=300, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "feeds_bank_feed_pull"
