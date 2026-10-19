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
