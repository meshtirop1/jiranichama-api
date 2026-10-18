import json
from datetime import date
from pathlib import Path

import pytest

from apps.rules import engine
from apps.rules.templates import template_for

GOLDEN = json.loads((Path(__file__).resolve().parents[1] / "golden" / "allocate_v1.json")
                    .read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda c: c["name"])
def test_allocate(case):
    rules = template_for("merry_go_round")
    rules["contributions"].update(case.get("contributions", {}))
    result = engine.allocate(
        rules=rules,
        amount_cents=case["amount_cents"],
        value_date=date.fromisoformat(case["value_date"]),
        dues=[engine.Due(p, o) for p, o in case["dues"]],
        fines=[engine.OpenFine(i, p, o) for i, p, o in case["fines"]],
    )
    got = [{k: v for k, v in s.as_dict().items() if k != "rule_refs"} for s in result.slices]
    assert got == case["slices"]
    assert result.fines_to_reverse == case["fines_to_reverse"]
    assert sum(s.amount_cents for s in result.slices) == case["amount_cents"]


def test_late_fine_respects_cap_and_switch():
    rules = template_for("merry_go_round")
    assert engine.late_fine_cents(rules) == 20_000
    rules["fines"]["late_contribution"]["monthly_cap_cents"] = 15_000
    assert engine.late_fine_cents(rules) == 15_000
    rules["fines"]["late_contribution"]["enabled"] = False
    assert engine.late_fine_cents(rules) == 0
