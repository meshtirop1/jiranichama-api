import json
from datetime import datetime
from pathlib import Path

from apps.core.canonical import canonical_json
from apps.ledger import hashing

CHAIN = json.loads(
    (Path(__file__).resolve().parents[3] / "vectors" / "ledger_chain.json")
    .read_text(encoding="utf-8")
)


def test_chain_vectors_recompute():
    prev = CHAIN["genesis_prev_hash"]
    assert prev == hashing.GENESIS_PREV_HASH
    assert CHAIN["hash_version"] == hashing.HASH_VERSION
    for entry in CHAIN["entries"]:
        doc = entry["document"]
        assert doc["prev_hash"] == prev
        assert hashing.signatures_digest(entry["signatures"]) == entry["signatures_digest"]
        rebuilt = hashing.entry_document(
            chama_id=doc["chama_id"], seq=doc["seq"], type=doc["type"],
            member_code=doc["member_code"], amount_cents=doc["amount_cents"],
            period=doc["period"], rule_version_id=doc["rule_version_id"],
            rule_refs=doc["rule_refs"], decision=doc["decision"], source=doc["source"],
            source_id=doc["source_id"], reverses=doc["reverses"],
            signatures_digest=doc["signatures_digest"],
            at=datetime.fromisoformat(doc["at"].replace("Z", "+00:00")),
            prev_hash=doc["prev_hash"],
        )
        assert rebuilt == doc
        assert canonical_json(doc) == entry["canonical"]
        assert hashing.entry_hash(doc) == entry["hash"]
        assert hashing.receipt_code(entry["hash"]) == entry["receipt_code"]
        prev = entry["hash"]


def test_document_has_exactly_the_hashed_fields():
    doc = CHAIN["entries"][0]["document"]
    assert tuple(sorted(doc)) == tuple(sorted(hashing.HASHED_FIELDS))
