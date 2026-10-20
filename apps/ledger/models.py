import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models

from apps.chamas.models import Chama, Membership


class EntryType(models.TextChoices):
    OPENING_BALANCE = "opening_balance"
    CONTRIBUTION = "contribution"
    CONTRIBUTION_PENDING_CASH = "contribution_pending_cash"
    CASH_BANKED = "cash_banked"
    FINE_RAISED = "fine_raised"
    FINE_WAIVED = "fine_waived"
    FINE_PAID = "fine_paid"
    PAYOUT = "payout"
    LOAN_DISBURSED = "loan_disbursed"
    BANK_CHARGE = "bank_charge"
    INTEREST_EARNED = "interest_earned"
    REVERSAL = "reversal"
    CORRECTION = "correction"
    UNAPPROVED_DEBIT_DETECTED = "unapproved_debit_detected"
    MEMBERSHIP = "membership"
    RULE_VERSION_ADOPTED = "rule_version_adopted"
    ROTATION_SET = "rotation_set"
    VOTE_RESULT = "vote_result"
    MEETING_CLOSED = "meeting_closed"
    MINUTES_SEALED = "minutes_sealed"
    LOAN_INTEREST = "loan_interest"
    LOAN_REPAYMENT = "loan_repayment"
    DISPUTE_RESOLVED = "dispute_resolved"
    INVESTMENT_RECORDED = "investment_recorded"
    VALUATION = "valuation"
    INVESTMENT_INCOME = "investment_income"
    REFERRAL_CONSENT = "referral_consent"
    SUPPORT_ACCESS = "support_access"
    DEVICE_REGISTERED = "device_registered"
    ROLE_CHANGED = "role_changed"
    SEAL = "seal"
    STATEMENT_ACCEPTED = "statement_accepted"
    BANK_MANDATE = "bank_mandate"


class LedgerEntry(models.Model):
    """One money fact (or governance fact) in a chama's chain. Written once, only by
    ``ledger.services.append``; the database refuses UPDATE and DELETE on this table."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="ledger_entries")
    seq = models.PositiveBigIntegerField()
    type = models.CharField(max_length=40, choices=EntryType.choices)
    member = models.ForeignKey(Membership, on_delete=models.PROTECT, null=True, blank=True,
                               related_name="ledger_entries")
    member_code = models.CharField(max_length=20, null=True, blank=True)
    amount_cents = models.BigIntegerField(default=0)
    period = models.CharField(max_length=7, null=True, blank=True)  # "2026-03"
    # The rules app (Phase 1) turns this into a FK to RuleVersion.
    rule_version_id = models.UUIDField(null=True, blank=True)
    rule_refs = ArrayField(models.CharField(max_length=32), default=list, blank=True)
    decision = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=24)
    source_id = models.UUIDField(null=True, blank=True)
    reverses = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True,
                                 related_name="reversed_by")
    signatures_digest = models.CharField(max_length=64, null=True, blank=True)
    at = models.DateTimeField()
    prev_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64, unique=True)
    receipt_code = models.CharField(max_length=8, db_index=True)

    class Meta:
        db_table = "ledger_entry"
        ordering = ["chama", "seq"]
        constraints = [
            models.UniqueConstraint(fields=["chama", "seq"], name="ledger_entry_chama_seq"),
        ]

    def __str__(self):
        return f"{self.chama_id}#{self.seq} {self.type}"


class ChainHead(models.Model):
    """The latest seq and hash per chama. Locked with SELECT ... FOR UPDATE on every
    append, so entries in one chama are written strictly one after another."""

    chama = models.OneToOneField(Chama, on_delete=models.PROTECT, primary_key=True,
                                 related_name="chain_head")
    seq = models.PositiveBigIntegerField(default=0)
    hash = models.CharField(max_length=64)

    class Meta:
        db_table = "ledger_chain_head"


class EntrySignature(models.Model):
    """The platform's signature over an entry hash, added after the entry commits so the
    KMS call never happens while the chain head is locked (6.6)."""

    entry = models.OneToOneField(LedgerEntry, on_delete=models.PROTECT, primary_key=True,
                                 related_name="platform_signature")
    platform_sig = models.BinaryField()
    key_id = models.CharField(max_length=200)
    signed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ledger_entry_signature"


class Anchor(models.Model):
    """One night's public anchor: a Merkle root over every chama's chain head
    (ledger.anchoring). Insert only, apart from the timestamp and email fields."""

    class OtsStatus(models.TextChoices):
        NONE = "none"
        SUBMITTED = "submitted"  # a calendar holds it; Bitcoin attestation follows
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    at = models.DateTimeField(auto_now_add=True)
    merkle_root = models.CharField(max_length=64)
    leaves = models.JSONField(default=list)  # [{chama_id, seq, hash}], sorted by chama
    ots_proof = models.BinaryField(null=True, blank=True)
    ots_calendar = models.CharField(max_length=200, blank=True, default="")
    ots_status = models.CharField(max_length=10, choices=OtsStatus.choices,
                                  default=OtsStatus.NONE)
    emailed = models.BooleanField(default=False)

    MUTABLE = ["ots_proof", "ots_calendar", "ots_status", "emailed"]

    class Meta:
        db_table = "ledger_anchor"


class PlatformKey(models.Model):
    """Every key that has ever signed records, in order. The first is the one the app is
    built to trust; each later key is endorsed by the key before it, so a phone accepts a
    new key only if the old one vouched for it (protection plan 2026-10-20). Insert only."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key_id = models.CharField(max_length=200, unique=True)
    fingerprint = models.CharField(max_length=64, unique=True)  # SHA-256 of the SPKI DER
    public_key_pem = models.TextField()
    prev_key_id = models.CharField(max_length=200, blank=True, default="")
    endorsement_sig = models.BinaryField(null=True, blank=True)  # by the previous key
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ledger_platform_key"


class HeadCountersignature(models.Model):
    """A member's phone vouching for a chama's records: "I checked this chain up to entry
    seq, and its hash was this", signed with the key in the phone's secure chip. Once
    members have signed a point in the history, nobody (not JiraniChama either) can
    replace it without every member's phone seeing that it differs. Insert only."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chama = models.ForeignKey(Chama, on_delete=models.PROTECT, related_name="+")
    membership = models.ForeignKey("chamas.Membership", on_delete=models.PROTECT,
                                   related_name="+")
    device = models.ForeignKey("devices.Device", on_delete=models.PROTECT, related_name="+")
    seq = models.PositiveBigIntegerField()
    hash = models.CharField(max_length=64)
    signature = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ledger_head_countersignature"
        constraints = [models.UniqueConstraint(fields=["membership", "seq"],
                                               name="countersign_once_per_member_and_seq")]
