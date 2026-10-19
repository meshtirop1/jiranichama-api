"""Investments with valuations (4.10, Phase 5)."""

import base64
import re
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.problems import Problem
from apps.feeds import services as feeds
from apps.feeds.models import BankTransaction
from apps.investments import services as investments
from apps.ledger.models import LedgerEntry
from apps.payouts import services as payouts
from apps.reconciliation.daily import reconcile

from .phase1 import active_chama, statement_csv

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


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
def chama(settings, sms_outbox):
    settings.BANK_STATEMENT_SENDERS = {"equity": ["equitybank.example"]}
    c, ms = active_chama(since=TODAY - timedelta(days=30), members=4)
    _statement(c, [(TODAY - timedelta(days=20), "OPENING DEPOSIT", 0, 50_000_000)], 0)
    for o in ms[:2]:
        ch = payouts.start_enable(c, o)
        payouts.enable(c, o, challenge_id=ch.id, sms_code=_code(sms_outbox))
    c.refresh_from_db()
    return c, ms


def _approve(p, by, outbox):
    ch = payouts.start_approve(p, by)
    return payouts.approve(p, by, challenge_id=ch.id, sms_code=_code(outbox))


def test_a_purchase_paid_through_money_out_becomes_an_investment(chama, sms_outbox):
    c, (chair, treasurer, secretary, member) = chama
    p = payouts.propose(c, chair, kind="withdrawal", amount_cents=4_000_000,
                        purpose="Plot in Juja", payee_name="Land seller")
    with pytest.raises(Problem) as e:
        investments.record(c, chair, kind="land", name="Juja plot", bought_on=TODAY,
                           cost_cents=None, purchase_id=p.id)
    assert e.value.problem_code == "purchase_not_paid"
    _approve(p, chair, sms_outbox)
    _approve(p, treasurer, sms_outbox)
    payouts.mark_paid(p, treasurer, "PLAND1")
    _statement(c, [(TODAY - timedelta(days=2), "PESALINK PLAND1", 4_000_000, 0)], 50_000_000)
    p.refresh_from_db()
    assert p.state == "paid"

    inv = investments.record(c, chair, kind="land", name="Juja plot", bought_on=TODAY,
                             cost_cents=None, purchase_id=p.id)
    assert inv.cost_cents == 4_000_000 and inv.entry.type == "investment_recorded"
    with pytest.raises(Problem):
        investments.record(c, chair, kind="land", name="Again", bought_on=TODAY,
                           cost_cents=None, purchase_id=p.id)
    with pytest.raises(Problem):
        investments.record(c, member, kind="shares", name="x", bought_on=TODAY,
                           cost_cents=100)


def test_a_valuation_counts_once_a_second_official_confirms_it(chama):
    c, (chair, treasurer, secretary, member) = chama
    inv = investments.record(c, chair, kind="shares", name="Safaricom shares",
                             bought_on=TODAY - timedelta(days=200), cost_cents=1_000_000)
    v = investments.value(inv, chair, value_cents=1_250_000, as_of=TODAY,
                          source="NSE closing price")
    assert investments.latest_value(inv) is None  # not yet confirmed
    with pytest.raises(Problem) as e:
        investments.confirm(v, chair)
    assert e.value.problem_code == "second_official_required"
    investments.confirm(v, treasurer)
    assert investments.latest_value(inv).value_cents == 1_250_000
    assert LedgerEntry.objects.get(type="valuation").decision["confirmed_by"] == \
        treasurer.member_code
    folio = investments.portfolio(c)
    assert folio["cost_cents"] == 1_000_000 and folio["value_cents"] == 1_250_000


def test_income_and_sale_come_in_through_the_bank(chama):
    c, (chair, treasurer, secretary, member) = chama
    inv = investments.record(c, chair, kind="mmf", name="CIC money market",
                             bought_on=TODAY - timedelta(days=90), cost_cents=5_000_000)
    _statement(c, [(TODAY - timedelta(days=1), "CIC ASSET MGT INTEREST", 0, 40_000),
                   (TODAY, "CIC ASSET MGT REDEMPTION", 0, 5_000_000)], 50_000_000)
    interest = BankTransaction.objects.get(chama=c, state="unmatched", amount_cents=40_000)
    sale = BankTransaction.objects.get(chama=c, state="unmatched", amount_cents=5_000_000)
    with pytest.raises(Problem):
        investments.add_income(inv, chair, txn_id=interest.id, kind="interest")
    investments.add_income(inv, treasurer, txn_id=interest.id, kind="interest")
    investments.add_income(inv, treasurer, txn_id=sale.id, kind="sale")
    inv.refresh_from_db()
    assert inv.state == "sold" and investments.income_cents(inv) == 5_040_000
    run = reconcile(c)
    # Only the unmatched opening deposit is left: the income is bank money on the records.
    assert run.gap_cents == run.unmatched_cents == 50_000_000


def test_the_portfolio_through_the_api(api, chama):
    c, (chair, treasurer, secretary, member) = chama
    api.force_authenticate(chair.user)
    r = api.post(f"/api/v1/chamas/{c.id}/investments/",
                 {"kind": "shares", "name": "KCB shares", "bought_on": TODAY.isoformat(),
                  "cost_cents": 300_000}, format="json")
    assert r.status_code == 201, r.content
    inv_id = r.json()["id"]
    r = api.post(f"/api/v1/investments/{inv_id}/valuations/",
                 {"value_cents": 350_000, "as_of": TODAY.isoformat(), "source": "NSE"},
                 format="json")
    vid = r.json()["valuations"][0]["id"]
    api.force_authenticate(treasurer.user)
    api.post(f"/api/v1/valuations/{vid}/confirm/")
    api.force_authenticate(member.user)
    folio = api.get(f"/api/v1/chamas/{c.id}/investments/").json()
    assert folio["value_cents"] == 350_000 and folio["investments"][0]["value_cents"] == 350_000
    assert folio["bank_cents"] is not None
