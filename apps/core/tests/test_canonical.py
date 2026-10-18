import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from apps.core.canonical import CanonicalError, canonical_json, iso_ms

VECTORS = json.loads(
    (Path(__file__).resolve().parents[3] / "vectors" / "canonical_json.json")
    .read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda c: c["name"])
def test_vector(case):
    out = canonical_json(json.loads(case["input"]))
    assert out == case["expected"]
    assert hashlib.sha256(out.encode()).hexdigest() == case["sha256"]


@pytest.mark.parametrize("case", VECTORS["must_reject"], ids=lambda c: c["name"])
def test_rejects(case):
    with pytest.raises(CanonicalError):
        canonical_json(json.loads(case["input"]))


def test_rejects_non_string_keys_and_lone_surrogates():
    with pytest.raises(CanonicalError):
        canonical_json({1: "x"})
    with pytest.raises(CanonicalError):
        canonical_json("\ud800")


def test_bool_is_not_an_integer():
    assert canonical_json([True, 1]) == "[true,1]"


def test_iso_ms_truncates_and_converts_to_utc():
    eat = timezone(timedelta(hours=3))
    dt = datetime(2026, 3, 4, 12, 15, 30, 123_999, tzinfo=eat)
    assert iso_ms(dt) == "2026-03-04T09:15:30.123Z"
    assert iso_ms(datetime(2026, 1, 1, tzinfo=UTC)) == "2026-01-01T00:00:00.000Z"
    with pytest.raises(CanonicalError):
        iso_ms(datetime(2026, 1, 1))
