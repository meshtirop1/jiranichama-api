"""Regenerate vectors/canonical_json.json and vectors/ledger_chain.json.

Run from the repo root:  python scripts/make_vectors.py
The output is committed and copied into the Flutter app (test/vectors/). If a change here
alters any hash, every existing chain breaks: bump HASH_VERSION instead.
"""

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from apps.core.canonical import CanonicalError, canonical_json  # noqa: E402
from apps.ledger import hashing  # noqa: E402

VECTORS = ROOT / "vectors"


def canonical_cases():
    source = json.loads((VECTORS / "canonical_json.source.json").read_text(encoding="utf-8"))
    cases = []
    for case in source["cases"]:
        got = canonical_json(json.loads(case["input"]))
        if got != case["expected"]:
            raise SystemExit(f"{case['name']}: expected {case['expected']!r}, got {got!r}")
        cases.append({**case, "sha256": hashlib.sha256(got.encode()).hexdigest()})
    for case in source["must_reject"]:
        try:
            canonical_json(json.loads(case["input"]))
        except CanonicalError:
            continue
        raise SystemExit(f"{case['name']}: should have been rejected")
    return {"about": source["about"], "cases": cases, "must_reject": source["must_reject"]}


CHAMA = "6f1c2a8e-3b4d-4c5e-9f60-7a8b9c0d1e2f"
MEMBER_CODE = "JC-4821-017"


def ledger_chain():
    inputs = [
        dict(type="opening_balance", member_code=MEMBER_CODE, amount_cents=1_250_000,
             period=None, rule_version_id=None, rule_refs=[], decision={},
             source="proposal", source_id="0b7a3c1d-2e4f-4a6b-8c9d-0e1f2a3b4c5d",
             reverses=None, signatures=[["JC-4821-001", "a1b2c3d4e5f60718", "MEUCIQ=="],
                                        ["JC-4821-002", "1817f6e5d4c3b2a1", "MEQCIA=="]],
             at=datetime(2026, 3, 1, 6, 0, 0, 0, tzinfo=UTC)),
        dict(type="contribution", member_code=MEMBER_CODE, amount_cents=100_000,
             period="2026-03", rule_version_id="9e8d7c6b-5a49-4382-a716-f5e4d3c2b1a0",
             rule_refs=["3.1"], decision={"outcome": "on_time"}, source="bank_txn",
             source_id="1c2d3e4f-5a6b-4c7d-8e9f-a0b1c2d3e4f5", reverses=None, signatures=[],
             at=datetime(2026, 3, 4, 9, 15, 30, 123_456, tzinfo=UTC)),
        dict(type="fine_raised", member_code=MEMBER_CODE, amount_cents=20_000,
             period="2026-03", rule_version_id="9e8d7c6b-5a49-4382-a716-f5e4d3c2b1a0",
             rule_refs=["3.2"], decision={"outcome": "late", "days_late": 4},
             source="scheduler", source_id=None, reverses=None, signatures=[],
             at=datetime(2026, 3, 8, 21, 0, 0, 999_999, tzinfo=UTC)),
        dict(type="correction", member_code=None, amount_cents=-20_000, period="2026-03",
             rule_version_id=None, rule_refs=[], decision={"reason": "Fine raised in error — "
                                                         "payment dated 4 Mar"},
             source="proposal", source_id="2d3e4f5a-6b7c-4d8e-9fa0-b1c2d3e4f5a6",
             reverses="__previous__", signatures=[["JC-4821-001", "a1b2c3d4e5f60718", "MEUCIQ=="]],
             at=datetime(2026, 3, 9, 7, 30, 0, 0, tzinfo=UTC)),
    ]
    prev = hashing.GENESIS_PREV_HASH
    entries = []
    previous_entry_id = None
    for seq, item in enumerate(inputs, start=1):
        entry_id = f"00000000-0000-4000-8000-{seq:012d}"
        reverses = previous_entry_id if item["reverses"] == "__previous__" else item["reverses"]
        digest = hashing.signatures_digest(item["signatures"])
        doc = hashing.entry_document(
            chama_id=CHAMA, seq=seq, type=item["type"], member_code=item["member_code"],
            amount_cents=item["amount_cents"], period=item["period"],
            rule_version_id=item["rule_version_id"], rule_refs=item["rule_refs"],
            decision=item["decision"], source=item["source"], source_id=item["source_id"],
            reverses=reverses, signatures_digest=digest, at=item["at"], prev_hash=prev,
        )
        h = hashing.entry_hash(doc)
        entries.append({
            "entry_id": entry_id,
            "signatures": item["signatures"],
            "signatures_digest": digest,
            "document": doc,
            "canonical": canonical_json(doc),
            "hash": h,
            "receipt_code": hashing.receipt_code(h),
        })
        prev = h
        previous_entry_id = entry_id
    return {
        "about": "One chama's chain. Recompute 'canonical' from 'document', 'hash' as SHA-256 "
                 "of it, 'receipt_code' as the first 8 Base32 characters of the hash bytes, "
                 "and check each prev_hash links to the hash before (genesis = 64 zeros).",
        "hash_version": hashing.HASH_VERSION,
        "genesis_prev_hash": hashing.GENESIS_PREV_HASH,
        "entries": entries,
    }


def platform_signatures(chain: dict):
    """Signatures over the chain vectors' hashes, made the way the platform signs
    (ECDSA P-256 over the 32-byte hash as a pre-hashed digest, DER). The key is derived
    from a public string: it is a TEST key and signs nothing real."""
    from cryptography.hazmat.primitives.asymmetric import ec

    from apps.ledger.signing import _PREHASHED, public_key_pem

    secret = int.from_bytes(hashlib.sha256(b"jiranichama test vector key").digest(), "big")
    key = ec.derive_private_key(secret % (2**256 - 2**224 - 1) or 1, ec.SECP256R1())
    public_pem = public_key_pem(key.public_key())
    import base64

    good = []
    for entry in chain["entries"]:
        sig = key.sign(bytes.fromhex(entry["hash"]), _PREHASHED)
        good.append({"hash": entry["hash"], "signature_b64": base64.b64encode(sig).decode()})
    first = good[0]
    bad = [
        {"name": "signature for another hash", "hash": good[1]["hash"],
         "signature_b64": first["signature_b64"]},
        {"name": "corrupted signature", "hash": first["hash"],
         "signature_b64": base64.b64encode(
             bytes([b ^ 0x01 if i == 10 else b for i, b in enumerate(
                 base64.b64decode(first["signature_b64"]))])).decode()},
        {"name": "not DER", "hash": first["hash"], "signature_b64": "AAAA"},
    ]
    return {
        "about": "Platform signatures: ECDSA P-256, SHA-256 pre-hashed (the message IS the "
                 "32-byte entry hash), DER-encoded, base64. The key is a TEST key derived "
                 "from a public string; production uses AWS KMS.",
        "public_key_pem": public_pem,
        "valid": good,
        "invalid": bad,
    }


def write(name, data):
    path = VECTORS / name
    # Always LF, so CI's regenerate-and-diff check passes on any platform.
    path.write_bytes((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    write("canonical_json.json", canonical_cases())
    chain = ledger_chain()
    write("ledger_chain.json", chain)
    signatures = VECTORS / "platform_signatures.json"
    if not signatures.exists():
        # ECDSA signatures are randomised, so this file is generated once and kept;
        # tests verify it rather than regenerate it.
        write("platform_signatures.json", platform_signatures(chain))
