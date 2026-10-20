"""The only code that writes money facts. See docs/flows/ledger-append.md."""

from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.chamas.models import Chama, Membership
from apps.core.canonical import truncate_ms
from apps.core.outbox import emit

from . import hashing
from .models import ChainHead, EntrySignature, LedgerEntry
from .signing import get_signer, verify_digest


def _locked_head(chama: Chama) -> ChainHead:
    try:
        return ChainHead.objects.select_for_update().get(chama=chama)
    except ChainHead.DoesNotExist:
        pass
    try:
        with transaction.atomic():
            ChainHead.objects.create(chama=chama, seq=0, hash=hashing.GENESIS_PREV_HASH)
    except IntegrityError:
        pass  # another writer created it first; we lock theirs below
    return ChainHead.objects.select_for_update().get(chama=chama)


def append(
    *,
    chama: Chama,
    type: str,
    source: str,
    amount_cents: int = 0,
    member: Membership | None = None,
    period: str | None = None,
    rule_version_id=None,
    rule_refs=(),
    decision: dict | None = None,
    source_id=None,
    reverses: LedgerEntry | None = None,
    signatures=(),
) -> LedgerEntry:
    """Append one entry to the chama's chain, inside the caller's transaction.

    No network call happens while the head is locked: the platform signature is added
    afterwards by the signing worker, triggered through the outbox.
    """
    if member is not None and member.chama_id != chama.id:
        raise ValueError("member belongs to another chama")
    if reverses is not None and reverses.chama_id != chama.id:
        raise ValueError("cannot reverse an entry from another chama")

    with transaction.atomic():
        head = _locked_head(chama)
        at = truncate_ms(timezone.now())
        member_code = member.member_code if member else None
        digest_of_signatures = hashing.signatures_digest(signatures)
        document = hashing.entry_document(
            chama_id=chama.id,
            seq=head.seq + 1,
            type=type,
            member_code=member_code,
            amount_cents=amount_cents,
            period=period,
            rule_version_id=rule_version_id,
            rule_refs=rule_refs,
            decision=decision,
            source=source,
            source_id=source_id,
            reverses=reverses.id if reverses else None,
            signatures_digest=digest_of_signatures,
            at=at,
            prev_hash=head.hash,
        )
        entry_hash = hashing.entry_hash(document)
        entry = LedgerEntry.objects.create(
            chama=chama,
            seq=document["seq"],
            type=type,
            member=member,
            member_code=member_code,
            amount_cents=amount_cents,
            period=period,
            rule_version_id=rule_version_id,
            rule_refs=list(rule_refs),
            decision=decision or {},
            source=source,
            source_id=source_id,
            reverses=reverses,
            signatures_digest=digest_of_signatures,
            at=at,
            prev_hash=head.hash,
            hash=entry_hash,
            receipt_code=hashing.receipt_code(entry_hash),
        )
        head.seq = entry.seq
        head.hash = entry_hash
        head.save(update_fields=["seq", "hash"])
        emit("ledger.entry_appended", {"entry_id": str(entry.id), "chama_id": str(chama.id)})
    return entry


def sign_entry(entry_id) -> EntrySignature | None:
    """Add the platform signature to an entry. Safe to call twice."""
    entry = LedgerEntry.objects.filter(pk=entry_id).first()
    if entry is None:
        return None
    existing = EntrySignature.objects.filter(entry=entry).first()
    if existing:
        return existing
    from .trust import ensure_current_key

    # Only a key on the endorsed chain signs; phones would reject any other.
    ensure_current_key()
    signer = get_signer()
    signature = signer.sign_digest(bytes.fromhex(entry.hash))
    try:
        with transaction.atomic():
            return EntrySignature.objects.create(entry=entry, platform_sig=signature,
                                                 key_id=signer.key_id)
    except IntegrityError:
        return EntrySignature.objects.get(entry=entry)


@dataclass
class ChainReport:
    chama_id: str
    entries: int = 0
    problems: list[str] = field(default_factory=list)
    unsigned: list[int] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def document_for(entry: LedgerEntry) -> dict:
    return hashing.entry_document(
        chama_id=entry.chama_id,
        seq=entry.seq,
        type=entry.type,
        member_code=entry.member_code,
        amount_cents=entry.amount_cents,
        period=entry.period,
        rule_version_id=entry.rule_version_id,
        rule_refs=entry.rule_refs,
        decision=entry.decision,
        source=entry.source,
        source_id=entry.source_id,
        reverses=entry.reverses_id,
        signatures_digest=entry.signatures_digest,
        at=entry.at,
        prev_hash=entry.prev_hash,
    )


def verify_chain(chama: Chama, check_signatures: bool = True) -> ChainReport:
    """Recompute every hash and link in a chama's chain, compare with its head, and check
    each platform signature. Any altered row is reported at its position."""
    report = ChainReport(chama_id=str(chama.id))
    public_key = get_signer().public_key() if check_signatures else None
    expected_prev = hashing.GENESIS_PREV_HASH
    expected_seq = 1
    entries = (LedgerEntry.objects.filter(chama=chama).order_by("seq")
               .select_related("platform_signature"))
    last_hash = hashing.GENESIS_PREV_HASH
    for entry in entries.iterator(chunk_size=2000):
        report.entries += 1
        if entry.seq != expected_seq:
            report.problems.append(f"seq {entry.seq}: expected seq {expected_seq}")
            expected_seq = entry.seq
        if entry.prev_hash != expected_prev:
            report.problems.append(f"seq {entry.seq}: prev_hash does not link to seq "
                                   f"{entry.seq - 1}")
        recomputed = hashing.entry_hash(document_for(entry))
        if recomputed != entry.hash:
            report.problems.append(f"seq {entry.seq}: contents do not match its hash")
        if check_signatures:
            sig = getattr(entry, "platform_signature", None)
            if sig is None:
                report.unsigned.append(entry.seq)
            elif not verify_digest(public_key, bytes.fromhex(entry.hash),
                                   bytes(sig.platform_sig)):
                report.problems.append(f"seq {entry.seq}: platform signature is not valid")
        expected_prev = entry.hash
        last_hash = entry.hash
        expected_seq += 1

    head = ChainHead.objects.filter(chama=chama).first()
    if head is not None and (head.seq != report.entries or head.hash != last_hash):
        report.problems.append(f"chain head says seq {head.seq}, chain has {report.entries}")
    return report
