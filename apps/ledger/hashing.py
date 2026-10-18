"""How a ledger entry is fingerprinted. Pure functions, shared with the Dart app through
``vectors/ledger_chain.json``. Changing anything here breaks every existing chain, so it
is versioned by ``HASH_VERSION`` and covered by the vector tests.
"""

import base64
import hashlib
from datetime import datetime

from apps.core.canonical import canonical_bytes, iso_ms

HASH_VERSION = 1
GENESIS_PREV_HASH = "0" * 64

HASHED_FIELDS = (
    "v",
    "chama_id",
    "seq",
    "type",
    "member_code",
    "amount_cents",
    "period",
    "rule_version_id",
    "rule_refs",
    "decision",
    "source",
    "source_id",
    "reverses",
    "signatures_digest",
    "at",
    "prev_hash",
)


def _id(value) -> str | None:
    return None if value is None else str(value).lower()


def entry_document(
    *,
    chama_id,
    seq: int,
    type: str,
    member_code: str | None,
    amount_cents: int,
    period: str | None,
    rule_version_id,
    rule_refs,
    decision: dict | None,
    source: str,
    source_id,
    reverses,
    signatures_digest: str | None,
    at: datetime,
    prev_hash: str,
) -> dict:
    """The exact object that is hashed. Ids are lower-case strings, ``at`` is ISO 8601 in
    UTC with milliseconds, and every field is always present (null when empty)."""
    return {
        "v": HASH_VERSION,
        "chama_id": _id(chama_id),
        "seq": seq,
        "type": type,
        "member_code": member_code,
        "amount_cents": amount_cents,
        "period": period,
        "rule_version_id": _id(rule_version_id),
        "rule_refs": list(rule_refs or []),
        "decision": decision or {},
        "source": source,
        "source_id": _id(source_id),
        "reverses": _id(reverses),
        "signatures_digest": signatures_digest,
        "at": iso_ms(at),
        "prev_hash": prev_hash,
    }


def entry_hash(document: dict) -> str:
    return hashlib.sha256(canonical_bytes(document)).hexdigest()


def receipt_code(hash_hex: str) -> str:
    """First 8 Base32 characters of the hash: what a member types after VERIFY."""
    return base64.b32encode(bytes.fromhex(hash_hex)).decode("ascii")[:8]


def signatures_digest(signatures) -> str | None:
    """SHA-256 over the sorted (member_code, device_key_fingerprint, signature_b64)
    triples that approved an entry; None when nobody signed."""
    # Sorted by UTF-16 code units, like canonical JSON keys, so Dart sorts the same way.
    triples = sorted([list(t) for t in signatures],
                     key=lambda t: [s.encode("utf-16-be") for s in t])
    if not triples:
        return None
    return hashlib.sha256(canonical_bytes(triples)).hexdigest()
