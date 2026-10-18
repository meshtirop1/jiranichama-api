"""Late fines, payments judged by their bank date, fines raised in error, and the
statement inbox. Built with fixed dates, so they do not depend on today."""

import base64
from datetime import date

import pytest

from apps.contributions.projections import position
from apps.feeds import services as feeds
from apps.feeds.models import Statement
from apps.fines.models import Fine
from apps.fines.services import raise_late_fines
from apps.ledger.models import LedgerEntry
from apps.reconciliation import services as recon
from apps.rules.templates import template_for

from .phase1 import active_chama, credit, statement_csv

pytestmark = pytest.mark.django_db

START = date(2026, 1, 1)


def test_late_fines_are_raised_once_per_period():
    chama, (chair, treasurer, secretary) = active_chama(since=START)
    assert raise_late_fines(chama, date(2026, 3, 10)) == 9  # Jan, Feb, Mar x 3 members
    assert raise_late_fines(chama, date(2026, 3, 10)) == 0  # safe to run twice
    fine = Fine.objects.get(membership=treasurer, period="2026-02")
    assert fine.amount_cents == 20_000
    assert fine.raised_entry.decision["grace_end"] == "2026-02-07"
    assert position(treasurer, date(2026, 3, 10)).fines_cents == 60_000


def test_fines_are_not_raised_inside_grace():
    chama, _ = active_chama(since=START)
    assert raise_late_fines(chama, date(2026, 1, 7)) == 0
    assert raise_late_fines(chama, date(2026, 1, 8)) == 3


def test_payment_goes_to_arrears_then_fines_by_its_bank_date():
    chama, (_, treasurer, _) = active_chama(since=START)
    raise_late_fines(chama, date(2026, 3, 10))
    txn = credit(chama, amount_cents=240_000, value_date=date(2026, 3, 9),
                 narration=f"pay {treasurer.member_code}")
    recon.match_credit(txn)
    entries = LedgerEntry.objects.filter(source_id=txn.id).order_by("seq")
    assert [(e.type, e.period, e.amount_cents) for e in entries] == [
        ("contribution", "2026-01", 100_000),
        ("contribution", "2026-02", 100_000),
        ("fine_paid", "2026-01", 20_000),
        ("fine_paid", "2026-02", 20_000),
    ]
    assert entries[0].decision["outcome"] == "late"
    assert Fine.objects.get(membership=treasurer, period="2026-01").state == "paid"


def test_on_time_payment_that_reached_us_late_reverses_its_fine():
    rules = template_for("merry_go_round")
    rules["contributions"]["allocation_order"] = ["current", "arrears", "fines"]
    chama, (_, treasurer, _) = active_chama(since=date(2026, 3, 1), rules=rules)
    raise_late_fines(chama, date(2026, 3, 12))  # the statement arrives on the 12th
    fine = Fine.objects.get(membership=treasurer, period="2026-03")

    txn = credit(chama, amount_cents=100_000, value_date=date(2026, 3, 6),
                 narration=f"{treasurer.member_code}")
    recon.match_credit(txn)
    fine.refresh_from_db()
    assert fine.state == "reversed"
    reversal = LedgerEntry.objects.get(source_id=txn.id, type="reversal")
    assert reversal.reverses_id == fine.raised_entry_id
    assert reversal.amount_cents == -20_000


def test_another_chamas_code_is_never_recorded_here():
    chama_a, _ = active_chama(since=START)
    chama_b, (_, b_treasurer, _) = active_chama(since=START)
    txn = credit(chama_a, amount_cents=100_000, value_date=date(2026, 2, 1),
                 narration=f"paid {b_treasurer.member_code}")
    recon.match_credit(txn)
    txn.refresh_from_db()
    assert txn.state == "unmatched"


def _mail(chama, data, *, sender="statements@equitybank.example", dkim="pass"):
    return {
        "to": chama.statement_inbox.address, "from": sender,
        "auth": {"spf": "pass", "dkim": dkim, "dmarc": "pass",
                 "dkim_domain": "equitybank.example"},
        "attachments": [{"filename": "statement.csv",
                         "content_b64": base64.b64encode(data).decode()}],
    }


def test_bank_email_to_the_inbox_is_accepted_without_anyone_touching_the_file(settings):
    settings.BANK_STATEMENT_SENDERS = {"equity": ["equitybank.example"]}
    chama, (_, treasurer, _) = active_chama(since=START)
    data = statement_csv([(date(2026, 2, 3), f"MPESA {treasurer.member_code}", 0, 100_000)],
                         opening_cents=0)
    [statement] = feeds.receive_mail(_mail(chama, data))
    assert statement.route == "bank_email" and statement.state == "accepted"
    assert statement.uploaded_by is None and statement.mail_auth["result"] == "ok"
    assert LedgerEntry.objects.filter(chama=chama, type="contribution").count() == 1


@pytest.mark.parametrize("senders,sender,dkim,why", [
    ({}, "statements@equitybank.example", "pass", "sender_not_configured"),
    ({"equity": ["equitybank.example"]}, "fraud@example.com", "pass", "sender_not_allowed"),
    ({"equity": ["equitybank.example"]}, "statements@equitybank.example", "fail",
     "dkim_failed"),
])
def test_unauthenticated_mail_is_held(settings, senders, sender, dkim, why):
    settings.BANK_STATEMENT_SENDERS = senders
    chama, _ = active_chama(since=START)
    data = statement_csv([(date(2026, 2, 3), "X", 0, 100_000)], opening_cents=0)
    [statement] = feeds.receive_mail(_mail(chama, data, sender=sender, dkim=dkim))
    assert statement.state == Statement.State.HELD_MAIL_CHECK
    assert statement.mail_auth["result"] == why
    assert not chama.bank_transactions.exists()


def test_overlapping_statement_skips_known_lines_and_catches_altered_ones(settings):
    settings.BANK_STATEMENT_SENDERS = {"equity": ["equitybank.example"]}
    chama, (_, treasurer, _) = active_chama(since=START)
    feb = [(date(2026, 2, 3), f"MPESA {treasurer.member_code}", 0, 100_000)]
    feeds.receive_mail(_mail(chama, statement_csv(feb, opening_cents=0)))

    longer = feb + [(date(2026, 2, 20), "CASH", 0, 5_000)]
    [ok] = feeds.receive_mail(_mail(chama, statement_csv(longer, opening_cents=0)))
    assert ok.state == "accepted"
    assert chama.bank_transactions.count() == 2  # the Feb 3 line was not counted twice

    altered = [(date(2026, 2, 3), f"MPESA {treasurer.member_code}", 0, 90_000),
               (date(2026, 2, 20), "CASH", 0, 5_000)]
    [bad] = feeds.receive_mail(_mail(chama, statement_csv(altered, opening_cents=0)))
    assert bad.state == "rejected"
    assert "overlap_differs" in [p["code"] for p in bad.problems]
