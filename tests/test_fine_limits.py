"""Fining stops after the rules' number of missed months (owner decision 2026-10-20), and
the monthly cap really limits all fines in a month."""

from datetime import date

import pytest

from apps.fines import limits
from apps.fines.models import Fine, FineStop
from apps.fines.services import raise_late_fines
from apps.reconciliation import services as recon
from apps.rules.book import render
from apps.rules.templates import template_for

from .phase1 import active_chama, credit

pytestmark = pytest.mark.django_db

START = date(2026, 1, 1)


def test_fines_stop_after_three_missed_months(monkeypatch):
    sent = []
    monkeypatch.setattr(limits, "send_sms", lambda to, text: sent.append((to, text)))
    chama, (chair, treasurer, secretary) = active_chama(since=START)
    raise_late_fines(chama, date(2026, 6, 10))  # Jan to Jun missed: six months
    fined = sorted(Fine.objects.filter(membership=treasurer).values_list("period", flat=True))
    assert fined == ["2026-01", "2026-02", "2026-03"]
    stops = sorted(FineStop.objects.filter(membership=treasurer)
                   .values_list("period", flat=True))
    assert stops == ["2026-04", "2026-05", "2026-06"]
    assert limits.stopped(treasurer, date(2026, 6, 10))
    # Each member is told once, with every official, not once per stopped month.
    assert sum("no more fines will be added" in t for _, t in sent) == 3
    assert raise_late_fines(chama, date(2026, 6, 10)) == 0


def test_a_stopped_month_is_never_fined_later():
    chama, (_, treasurer, _) = active_chama(since=START)
    raise_late_fines(chama, date(2026, 5, 10))  # Jan to Mar fined, Apr and May stopped
    # The member catches up on January to March; April and May stay unfined.
    txn = credit(chama, amount_cents=360_000, value_date=date(2026, 5, 11),
                 narration=f"pay {treasurer.member_code}")
    recon.match_credit(txn)
    raise_late_fines(chama, date(2026, 5, 12))
    fined = set(Fine.objects.filter(membership=treasurer).values_list("period", flat=True))
    assert "2026-04" not in fined and "2026-05" not in fined


def test_the_number_of_months_comes_from_the_rules():
    rules = template_for("table_banking")
    rules["fines"]["stop_after_missed_months"] = 1
    chama, (_, treasurer, _) = active_chama(since=START, rules=rules)
    raise_late_fines(chama, date(2026, 3, 10))
    assert list(Fine.objects.filter(membership=treasurer).values_list("period", flat=True)) \
        == ["2026-01"]


def test_the_monthly_cap_limits_all_fines_in_a_month():
    rules = template_for("table_banking")
    rules["fines"]["late_contribution"]["monthly_cap_cents"] = 25_000
    chama, (_, treasurer, _) = active_chama(since=START, rules=rules)
    # A meeting fine of KES 150 already raised for February.
    from apps.ledger import services as ledger
    entry = ledger.append(chama=chama, type="fine_raised", source="meeting",
                          member=treasurer, amount_cents=15_000, period="2026-02",
                          rule_refs=["7.2"], decision={"cause": "meeting_absence"})
    Fine.objects.create(chama=chama, membership=treasurer, rule_ref="7.2", period="2026-02",
                        amount_cents=15_000, raised_entry=entry)
    raise_late_fines(chama, date(2026, 2, 10))
    late = Fine.objects.get(membership=treasurer, period="2026-02", rule_ref="3.2")
    assert late.amount_cents == 10_000  # the cap of 250 less the 150 already fined


def test_the_rule_book_says_when_fines_stop():
    clauses = {c.ref: c.text for c in render(template_for("table_banking"), "en")}
    assert "missed 3 monthly contributions" in clauses["3.6"]
    sw = {c.ref: c.text for c in render(template_for("table_banking"), "sw")}
    assert "michango 3" in sw["3.6"]
