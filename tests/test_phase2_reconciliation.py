"""Daily reconciliation: the bank against the records, gaps every member sees, and a
breach after 7 days (5.5)."""

import base64
import re
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.chamas.models import Membership
from apps.feeds import services as feeds
from apps.ledger import services as ledger
from apps.payouts import services as payouts
from apps.reconciliation import daily
from apps.reconciliation import services as recon
from apps.reconciliation.models import ReconciliationGap, ReconciliationRun

from .phase1 import active_chama, make_user, statement_csv

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


@pytest.fixture(autouse=True)
def mail(settings):
    settings.BANK_STATEMENT_SENDERS = {"equity": ["equitybank.example"]}


def _statement(chama, lines, opening_cents, name="s.csv"):
    data = statement_csv(lines, opening_cents=opening_cents)
    [s] = feeds.receive_mail({
        "to": chama.statement_inbox.address, "from": "statements@equitybank.example",
        "auth": {"spf": "pass", "dkim": "pass", "dmarc": "pass",
                 "dkim_domain": "equitybank.example"},
        "attachments": [{"filename": name, "content_b64": base64.b64encode(data).decode()}],
    })
    assert s.state == "accepted", s.problems
    return s


def test_matched_payments_mean_the_records_match_the_bank():
    c, (chair, treasurer, _) = active_chama(since=TODAY)
    _statement(c, [(TODAY, f"MPESA {treasurer.member_code}", 0, 100_000),
                   (TODAY, f"MPESA {chair.member_code}", 0, 100_000),
                   (TODAY, "LEDGER FEE", 1_500, 0)], 500_000)
    s = daily.summary(c)  # reconciled straight after the statement was accepted
    assert s["status"] == "match"
    assert s["bank_cents"] == 698_500 == s["records_cents"]
    assert s["checked_on"] == TODAY.isoformat()


def test_an_unmatched_payment_is_a_gap_until_it_is_assigned():
    c, (chair, treasurer, _) = active_chama(since=TODAY)
    _statement(c, [(TODAY, "CASH DEPOSIT", 0, 30_000)], 0)
    s = daily.summary(c)
    assert s["status"] == "open" and s["gap_cents"] == 30_000
    assert s["unmatched_cents"] == 30_000 and s["unexplained_cents"] == 0
    gap = ReconciliationGap.objects.get(chama=c)
    assert gap.opened_on == TODAY

    txn = c.bank_transactions.get(amount_cents=30_000)
    recon.assign(txn, chair, by=treasurer, reason="Jane paid cash at the bank")
    daily.reconcile(c)
    assert daily.summary(c)["status"] == "match"
    gap.refresh_from_db()
    assert gap.state == "explained" and gap.explained_on == TODAY


def test_a_gap_open_more_than_7_days_is_a_breach_and_every_member_is_told(
        sms_outbox, django_capture_on_commit_callbacks):
    c, (chair, treasurer, _) = active_chama(since=TODAY)
    _statement(c, [(TODAY, "UNKNOWN DEPOSIT", 0, 45_000)], 0)
    daily.reconcile(c, TODAY + timedelta(days=7))
    assert ReconciliationGap.objects.get(chama=c).state == "open"  # 7 days is not more

    before = len(sms_outbox)
    with django_capture_on_commit_callbacks(execute=True):
        daily.reconcile(c, TODAY + timedelta(days=8))
    gap = ReconciliationGap.objects.get(chama=c)
    assert gap.state == "breach" and gap.alerted == 3
    alerts = sms_outbox[before:]
    assert len(alerts) == 3 and all("BREACH" in t and "KES 450" in t for _, t in alerts)

    with django_capture_on_commit_callbacks(execute=True):
        daily.reconcile(c, TODAY + timedelta(days=9))
    assert len(sms_outbox) == before + 3  # told once, not every morning

    recon.assign(c.bank_transactions.get(amount_cents=45_000), chair, by=treasurer,
                 reason="Chair's deposit")
    daily.reconcile(c, TODAY + timedelta(days=10))
    gap.refresh_from_db()
    assert gap.state == "explained"


def test_not_me_reopens_the_gap():
    c, (chair, treasurer, _) = active_chama(since=TODAY)
    _statement(c, [(TODAY, "CASH DEPOSIT", 0, 100_000)], 0)
    txn = c.bank_transactions.get()
    recon.assign(txn, chair, by=treasurer, reason="Looks like Jane")
    daily.reconcile(c)
    assert daily.summary(c)["status"] == "match"
    recon.not_me(txn, chair, reason="Not mine")
    daily.reconcile(c)
    assert daily.summary(c)["gap_cents"] == 100_000


def test_bookkeeping_entries_do_not_move_the_bank():
    """Opening balances, fines raised and their reversals are not money in the bank."""
    c, (chair, *_) = active_chama(since=TODAY)
    _statement(c, [(TODAY, f"MPESA {chair.member_code}", 0, 100_000)], 0)
    ledger.append(chama=c, type="opening_balance", source="opening", member=chair,
                  amount_cents=800_000)
    raised = ledger.append(chama=c, type="fine_raised", source="fines", member=chair,
                           amount_cents=20_000)
    ledger.append(chama=c, type="reversal", source="fines", member=chair,
                  amount_cents=-20_000, reverses=raised)
    daily.reconcile(c)
    assert daily.summary(c)["status"] == "match"


def test_money_out_matched_and_unapproved_debits_are_both_in_the_records(sms_outbox):
    c, (chair, treasurer, secretary) = active_chama(since=TODAY)
    member = Membership.objects.create(chama=c, user=make_user("+254799000777", "Mary"),
                                       joined_on=TODAY, member_code=f"{c.member_code_prefix}-004")
    _statement(c, [(TODAY - timedelta(days=2), "DEPOSIT", 0, 10_000_000)], 0, "a.csv")
    assert daily.summary(c)["gap_cents"] == 10_000_000  # nobody's yet
    for o in (chair, treasurer):
        ch = payouts.start_enable(c, o)
        payouts.enable(c, o, challenge_id=ch.id, sms_code=_code(sms_outbox))
    c.refresh_from_db()
    p = payouts.propose(c, chair, kind="payout", amount_cents=2_000_000, payee=member,
                        purpose="Turn")
    for o in (chair, treasurer):
        ch = payouts.start_approve(p, o)
        payouts.approve(p, o, challenge_id=ch.id, sms_code=_code(sms_outbox))
    _statement(c, [(TODAY - timedelta(days=1), "TRANSFER MARY", 2_000_000, 0),
                   (TODAY - timedelta(days=1), "CASH WITHDRAWAL", 500_000, 0)],
               10_000_000, "b.csv")
    s = daily.summary(c)
    # The payout and the unapproved debit are both on the records; only the first
    # deposit, still unassigned, is the gap.
    assert s["bank_cents"] == 7_500_000
    assert s["gap_cents"] == 10_000_000 == s["unmatched_cents"]


def test_records_only_chamas_count_waiting_debits_as_accounted():
    c, (chair, *_) = active_chama(since=TODAY)
    _statement(c, [(TODAY, f"MPESA {chair.member_code}", 0, 100_000),
                   (TODAY, "WITHDRAWAL", 50_000, 0)], 0)
    assert daily.summary(c)["status"] == "match"


def test_the_daily_task_checks_every_active_chama(api):
    c1, (chair, *_) = active_chama(since=TODAY)
    active_chama(since=TODAY)  # no statement yet: nothing to check
    _statement(c1, [(TODAY, f"MPESA {chair.member_code}", 0, 100_000)], 0)
    before = ReconciliationRun.objects.count()
    assert daily.reconcile_all() == 1
    assert ReconciliationRun.objects.count() == before + 1

    api.force_authenticate(chair.user)
    r = api.get(f"/api/v1/chamas/{c1.id}/reconciliation/")
    assert r.status_code == 200 and r.json()["status"] == "match"
    assert r.json()["history"][0]["gap_cents"] == 0


def test_a_chama_with_no_statement_says_so(api):
    c, (chair, *_) = active_chama(since=TODAY)
    api.force_authenticate(chair.user)
    assert api.get(f"/api/v1/chamas/{c.id}/reconciliation/").json()["status"] == "no_statement"
