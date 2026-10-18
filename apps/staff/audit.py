import hashlib

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.canonical import canonical_bytes, iso_ms, truncate_ms

from .models import AuditHead, AuditLog

GENESIS = "0" * 64


class MissingReason(ValueError):
    pass


def audit_document(entry: AuditLog) -> dict:
    return {
        "seq": entry.seq,
        "actor_id": str(entry.actor_id) if entry.actor_id else None,
        "action": entry.action,
        "target": entry.target,
        "reason": entry.reason,
        "case_ref": entry.case_ref,
        "ip": entry.ip,
        "at": iso_ms(entry.at),
        "prev_hash": entry.prev_hash,
    }


def _hash(entry: AuditLog) -> str:
    return hashlib.sha256(canonical_bytes(audit_document(entry))).hexdigest()


def _locked_head() -> AuditHead:
    try:
        return AuditHead.objects.select_for_update().get(pk=1)
    except AuditHead.DoesNotExist:
        pass
    try:
        with transaction.atomic():
            AuditHead.objects.create(pk=1, seq=0, hash=GENESIS)
    except IntegrityError:
        pass
    return AuditHead.objects.select_for_update().get(pk=1)


def record(*, actor, action: str, reason: str, case_ref: str, target: str = "",
           ip: str | None = None) -> AuditLog:
    """Write one audit entry. A reason and a ticket reference are always required."""
    if not (reason or "").strip() or not (case_ref or "").strip():
        raise MissingReason("staff actions need a reason and a ticket reference")
    with transaction.atomic():
        head = _locked_head()
        entry = AuditLog(
            seq=head.seq + 1,
            actor=actor,
            action=action,
            target=target,
            reason=reason.strip(),
            case_ref=case_ref.strip(),
            ip=ip,
            at=truncate_ms(timezone.now()),
            prev_hash=head.hash,
        )
        entry.hash = _hash(entry)
        entry.save()
        head.seq, head.hash = entry.seq, entry.hash
        head.save(update_fields=["seq", "hash"])
    return entry


def verify_audit_chain() -> list[str]:
    problems, prev, expected = [], GENESIS, 1
    for entry in AuditLog.objects.order_by("seq").iterator():
        if entry.seq != expected:
            problems.append(f"seq {entry.seq}: expected {expected}")
            expected = entry.seq
        if entry.prev_hash != prev:
            problems.append(f"seq {entry.seq}: prev_hash does not link")
        if _hash(entry) != entry.hash:
            problems.append(f"seq {entry.seq}: contents do not match its hash")
        prev, expected = entry.hash, expected + 1
    return problems
