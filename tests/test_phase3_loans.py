"""Loans with guarantors (5.8), behind the "loans" flag."""

import base64
import re
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.contributions.projections import dues
from apps.core.problems import Problem
from apps.feeds import services as feeds
from apps.fines.models import Fine
from apps.flags.models import FeatureFlag
from apps.ledger import services as ledger
from apps.ledger.models import LedgerEntry
from apps.ledger.services import verify_chain
from apps.loans import schedule as sched
from apps.loans import services as loans
from apps.loans.models import Guarantee, Loan
from apps.payouts import services as payouts
from apps.reconciliation import services as recon
from apps.rules import calendar
from apps.rules.templates import template_for

from .phase1 import active_chama, credit, idem, statement_csv

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


def _rules(**loan):
    r = template_for("table_banking")
    r["loans"].update(loan)
    return r


def _save(m, opening_cents):
    """Savings brought in as an opening balance, and every month so far paid."""
    ledger.append(chama=m.chama, type="opening_balance", source="test", member=m,
                  amount_cents=opening_cents)
    current = calendar.period_of(TODAY)
    for d in dues(m, TODAY):
        if d.period <= current and d.outstanding_cents:
            ledger.append(chama=m.chama, type="contribution", source="test", member=m,
                          amount_cents=d.outstanding_cents, period=d.period)


def _statement(chama, lines, opening_cents):
    data = statement_csv(lines, opening_cents=opening_cents)
    [s] = feeds.receive_mail({
        "to": chama.statement_inbox.address, "from": "statements@equitybank.example",
        "auth": {"spf": "pass", "dkim": "pass", "dmarc": "pass",
                 "dkim_domain": "equitybank.example"},
        "attachments": [{"filename": "s.csv", "content_b64": base64.b64encode(data).decode()}],
    })
    assert s.state == "accepted", s.problems
    return s


@pytest.fixture
def setup(settings, sms_outbox):
    settings.BANK_STATEMENT_SENDERS = {"equity": ["equitybank.example"]}
    c, (chair, treasurer, secretary, mary, john) = active_chama(
        since=TODAY - timedelta(days=150), members=5, rules=_rules(late_penalty_cents=20_000))
    for m in (chair, treasurer, secretary, mary, john):
        _save(m, 2_000_000)  # KES 20,000 each
    FeatureFlag.objects.create(key="loans", chama_allowlist=[c.id])
    _statement(c, [(TODAY - timedelta(days=10), "DEPOSIT", 0, 50_000_000)], 0)
    for o in (chair, treasurer):
        ch = payouts.start_enable(c, o)
        payouts.enable(c, o, challenge_id=ch.id, sms_code=_code(sms_outbox))
    c.refresh_from_db()
    return c, chair, treasurer, secretary, mary, john


def _guarantee(g, outbox):
    ch = loans.start_accept(g, g.guarantor)
    assert "GUARANTEE KES 30,000" in outbox[-1][1] and "your share KES 15,000" in outbox[-1][1]
    return loans.accept(g, g.guarantor, challenge_id=ch.id, sms_code=_code(outbox))


def _approve(p, by, outbox):
    ch = payouts.start_approve(p, by)
    return payouts.approve(p, by, challenge_id=ch.id, sms_code=_code(outbox))


def test_loans_are_off_without_the_flag(settings):
    c, (chair, *_) = active_chama(since=TODAY, rules=_rules())
    with pytest.raises(Problem) as e:
        loans.quote(c, chair, amount_cents=100_000, term_months=3)
    assert e.value.problem_code == "loans_off"


def test_schedules_flat_and_reducing():
    s = {**sched.DEFAULTS, "monthly_rate_bp": 100}
    flat = sched.build(amount_cents=3_000_000, term_months=3, s=s, start=TODAY)
    assert [r.principal_cents for r in flat] == [1_000_000] * 3
    assert [r.interest_cents for r in flat] == [30_000] * 3
    red = sched.build(amount_cents=3_000_000, term_months=3, s={**s, "interest": "reducing"},
                      start=TODAY)
    assert [r.interest_cents for r in red] == [30_000, 20_000, 10_000]
    odd = sched.build(amount_cents=1_000_000, term_months=3, s=s, start=TODAY)
    assert sum(r.principal_cents for r in odd) == 1_000_000 and odd[-1].principal_cents == 333_334


def test_quote_shows_the_limit_terms_and_reasons(setup):
    c, chair, treasurer, secretary, mary, john = setup
    q = loans.quote(c, mary, amount_cents=3_000_000, term_months=6)
    assert q["eligible"] and q["guarantors_needed"] == 2 and q["share_cents"] == 1_500_000
    assert q["limit_cents"] == 3 * q["savings_cents"]
    assert len(q["schedule"]) == 6 and q["total_interest_cents"] == 6 * 30_000
    q = loans.quote(c, mary, amount_cents=q["limit_cents"] + 1, term_months=24)
    assert {r["code"] for r in q["reasons"]} == {"over_limit", "term_too_long"}


def test_guarantors_accept_by_code_and_the_loan_goes_to_the_officials(setup, sms_outbox):
    c, chair, treasurer, secretary, mary, john = setup
    with pytest.raises(Problem) as e:
        loans.request(c, mary, amount_cents=3_000_000, term_months=6, purpose="Stock",
                      guarantor_ids=[john.id])
    assert e.value.problem_code == "guarantors_needed"
    loan = loans.request(c, mary, amount_cents=3_000_000, term_months=6, purpose="Stock",
                         guarantor_ids=[john.id, secretary.id])
    assert loan.state == "awaiting_guarantors"
    g1, g2 = loan.guarantees.get(guarantor=john), loan.guarantees.get(guarantor=secretary)
    with pytest.raises(Problem):
        loans.start_accept(g1, mary)  # only the guarantor answers
    _guarantee(g1, sms_outbox)
    loan.refresh_from_db()
    assert loan.state == "awaiting_guarantors"

    loans.decline(g2, secretary)
    assert "declined to guarantee" in sms_outbox[-1][1]
    loans.replace_guarantor(loan, mary, guarantee_id=g2.id, membership_id=treasurer.id)
    g3 = loan.guarantees.get(guarantor=treasurer)
    _guarantee(g3, sms_outbox)
    loan.refresh_from_db()
    assert loan.state == "awaiting_approval"
    p = loan.proposal
    assert p.kind == "loan" and p.payee_membership_id == mary.id
    assert loans.guaranteed_cents(john) == 1_500_000  # john's own limit drops
    q = loans.quote(c, john, amount_cents=100_000, term_months=3)
    assert q["limit_cents"] == 3 * q["savings_cents"] - 1_500_000

    _approve(p, chair, sms_outbox)
    _approve(p, treasurer, sms_outbox)
    loans.sync(loan)
    assert loan.state == "approved"


def _disbursed(setup, sms_outbox, **kw):
    c, chair, treasurer, secretary, mary, john = setup
    loan = loans.request(c, mary, amount_cents=3_000_000, term_months=3, purpose="Stock",
                         guarantor_ids=[john.id, secretary.id])
    for g in loan.guarantees.all():
        _guarantee(g, sms_outbox)
    loan.refresh_from_db()
    p = loan.proposal
    _approve(p, chair, sms_outbox)
    _approve(p, treasurer, sms_outbox)
    payouts.mark_paid(p, treasurer, "PL1")
    paid_on = TODAY - timedelta(days=1)
    _statement(c, [(paid_on, "PESALINK PL1 MARY", 3_000_000, 0)], 50_000_000)
    loan.refresh_from_db()
    return loan, paid_on


def test_disbursement_starts_the_schedule_and_is_on_the_ledger(setup, sms_outbox):
    loan, paid_on = _disbursed(setup, sms_outbox)
    assert loan.state == "repaying" and loan.disbursed_on == paid_on
    entry = LedgerEntry.objects.get(type="loan_disbursed", decision__loan_id=str(loan.id))
    assert entry.amount_cents == -3_000_000
    assert loan.schedule[0]["due"] == sched._add_months(paid_on, 1).isoformat()
    assert "your loan of KES 30,000 is paid out" in sms_outbox[-1][1]
    assert verify_chain(loan.chama).ok


def test_interest_on_due_dates_repayment_split_and_repaid(setup, sms_outbox):
    c, chair, treasurer, secretary, mary, john = setup
    loan, paid_on = _disbursed(setup, sms_outbox)
    first = sched.from_dicts(loan.schedule)[0]
    loans.run_daily(first.due)
    loans.run_daily(first.due)  # twice: still charged once
    assert loans.balance(loan, first.due)["interest_charged_cents"] == 30_000
    assert loans.balance(loan, first.due)["due_now_cents"] == 1_030_000

    # Mary pays her contribution and the instalment in one payment: contributions first.
    monthly = c.rule_versions.get(version_no=1).rules["contributions"]["amount_cents"]
    txn = credit(c, amount_cents=1_030_000 + monthly, value_date=first.due,
                 narration=mary.member_code)
    entries = recon.record(txn, mary, method="member_code")
    kinds = sorted((e.type, e.amount_cents) for e in entries)
    assert ("loan_repayment", 1_030_000) in kinds
    b = loans.balance(loan, first.due)
    assert b["repaid_cents"] == 1_030_000 and b["due_now_cents"] == 0

    # The rest, at the end of the term.
    rows = sched.from_dicts(loan.schedule)
    loans.run_daily(rows[-1].due)
    rest = loans.balance(loan, rows[-1].due)["outstanding_cents"]
    assert rest == 2_060_000
    for r in rows[1:]:
        ledger.append(chama=c, type="loan_repayment", source="test", member=mary,
                      amount_cents=r.total_cents, decision={"loan_id": str(loan.id)})
    loans.settle(loan.pk)
    loan.refresh_from_db()
    assert loan.state == "repaid"
    assert set(loan.guarantees.values_list("state", flat=True)) == {"released"}
    assert "repaid in full" in sms_outbox[-1][1]


def test_missed_instalments_penalty_arrears_then_default(setup, sms_outbox):
    c, chair, treasurer, secretary, mary, john = setup
    loan, _ = _disbursed(setup, sms_outbox)
    rows = sched.from_dicts(loan.schedule)
    loans.run_daily(rows[0].due + timedelta(days=4))  # past the 3 grace days
    loan.refresh_from_db()
    assert loan.state == "in_arrears"
    assert Fine.objects.filter(membership=mary, rule_ref="12.3").count() == 1
    loans.run_daily(rows[1].due + timedelta(days=4))
    loan.refresh_from_db()
    assert loan.state == "defaulted"
    assert set(loan.guarantees.values_list("state", flat=True)) == {"called"}
    assert any("in default" in t for _, t in sms_outbox)
    sent = len(sms_outbox)
    loans.run_daily(rows[1].due + timedelta(days=5))
    assert not any("in default" in t for _, t in sms_outbox[sent:])  # told once


def test_unanswered_guarantees_expire_and_requests_lapse(setup):
    c, chair, treasurer, secretary, mary, john = setup
    loan = loans.request(c, mary, amount_cents=3_000_000, term_months=3, purpose="Stock",
                         guarantor_ids=[john.id, secretary.id])
    later = timezone.now() + timedelta(days=8)
    import apps.loans.services as mod

    orig = mod.timezone.now
    mod.timezone.now = lambda: later
    try:
        loans.run_daily(TODAY + timedelta(days=8))
    finally:
        mod.timezone.now = orig
    loan.refresh_from_db()
    assert loan.state == "expired"
    assert set(Guarantee.objects.filter(loan=loan).values_list("state", flat=True)) == {
        "expired"}


def test_loans_through_the_api(api, setup, sms_outbox):
    c, chair, treasurer, secretary, mary, john = setup
    api.force_authenticate(mary.user)
    r = api.get(f"/api/v1/chamas/{c.id}/loans/quote/?amount_cents=3000000&term_months=3")
    assert r.json()["eligible"] is True
    r = api.post(f"/api/v1/chamas/{c.id}/loans/",
                 {"amount_cents": 3_000_000, "term_months": 3, "purpose": "Stock",
                  "guarantor_ids": [str(john.id), str(secretary.id)]}, format="json", **idem())
    assert r.status_code == 201, r.content
    loan_id = r.json()["id"]
    g = next(g for g in r.json()["guarantees"] if g["guarantor_id"] == str(john.id))

    api.force_authenticate(john.user)
    r = api.post(f"/api/v1/guarantees/{g['id']}/accept/", {}, format="json")
    assert r.status_code == 202
    r = api.post(f"/api/v1/guarantees/{g['id']}/accept/",
                 {"challenge_id": r.json()["challenge_id"], "sms_code": _code(sms_outbox)},
                 format="json")
    assert r.status_code == 200
    mine = [x for x in r.json()["guarantees"] if x["mine"]]
    assert mine[0]["state"] == "accepted"
    listed = api.get(f"/api/v1/chamas/{c.id}/loans/").json()
    assert listed[0]["id"] == loan_id and listed[0]["balance"]["outstanding_cents"] == 0
    assert Loan.objects.get(pk=loan_id).state == "awaiting_guarantors"
