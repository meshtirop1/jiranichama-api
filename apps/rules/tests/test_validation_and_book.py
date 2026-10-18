import copy
from datetime import date

import pytest

from apps.rules import book, calendar, validation
from apps.rules.templates import TEMPLATES, template_for


@pytest.mark.parametrize("chama_type", sorted(TEMPLATES))
def test_every_template_is_valid(chama_type):
    assert validation.check(template_for(chama_type)) == []


def _codes(rules):
    return [e.path for e in validation.check(rules)]


def test_schema_refuses_bad_shapes():
    r = template_for("welfare")
    r["contributions"]["due_day"] = 31
    r["contributions"]["frequency"] = "weekly"
    r["surprise"] = True
    paths = _codes(r)
    assert "contributions/due_day" in paths
    assert "contributions/frequency" in paths
    assert "(root)" in paths


def test_floats_are_refused_for_money():
    r = template_for("welfare")
    r["contributions"]["amount_cents"] = 1000.5
    assert "contributions/amount_cents" in _codes(r)


def test_payee_can_never_be_required_to_approve():
    r = template_for("merry_go_round")  # 3 officials
    r["approvals"]["tiers"][0]["officials"] = 3
    errors = validation.check(r)
    assert any("nobody approves a payout to themselves" in e.message for e in errors)


def test_tiers_must_be_ordered_and_end_open():
    r = template_for("merry_go_round")
    r["approvals"]["tiers"] = [
        {"up_to_cents": 9_000_000, "officials": 2, "member_vote": False},
        {"up_to_cents": 5_000_000, "officials": 1, "member_vote": False},
    ]
    messages = [e.message for e in validation.check(r)]
    assert any("last tier" in m for m in messages)
    assert any("go up in order" in m for m in messages)
    assert any("fewer officials" in m for m in messages)


def test_fine_cap_below_one_fine_is_a_contradiction():
    r = template_for("merry_go_round")
    r["fines"]["late_contribution"]["monthly_cap_cents"] = 100
    assert "fines/late_contribution/monthly_cap_cents" in _codes(r)


def test_validate_raises_with_all_errors():
    r = copy.deepcopy(template_for("merry_go_round"))
    r["approvals"]["tiers"][0]["officials"] = 5
    with pytest.raises(validation.RulesInvalid) as exc:
        validation.validate(r)
    assert exc.value.errors


@pytest.mark.parametrize("chama_type", sorted(TEMPLATES))
def test_rule_book_has_the_same_clauses_in_both_languages(chama_type):
    rules = template_for(chama_type)
    en = book.render(rules, "en")
    sw = book.render(rules, "sw")
    assert [c.ref for c in en] == [c.ref for c in sw]
    assert all(c.text.strip() for c in en + sw)


def test_rule_book_states_the_rules_in_plain_words():
    text = {c.ref: c.text for c in book.render(template_for("merry_go_round"), "en")}
    assert text["3.1"] == ("Each member pays KES 1,000 every month, by the 5th. A payment is "
                           "on time up to 2 days after that.")
    assert text["3.2"] == "A contribution paid after that is late. The fine is KES 200."
    assert "P.1" in text and "themselves" in text["P.1"]


def test_calendar():
    rules = template_for("merry_go_round")
    assert calendar.first_period(date(2026, 3, 5), rules) == "2026-03"
    assert calendar.first_period(date(2026, 3, 6), rules) == "2026-04"
    assert calendar.next_period("2026-12") == "2027-01"
    assert calendar.grace_end("2026-02", rules) == date(2026, 2, 7)
    assert calendar.periods("2026-11", "2027-02") == ["2026-11", "2026-12", "2027-01",
                                                      "2027-02"]
