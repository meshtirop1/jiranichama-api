"""The chama runs itself (owner request 2026-10-21): notices sent once, automatic chasing,
payment reminders, the monthly pack and the bill shortcut."""

import re
from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.automation import chasers, monthly, reminders
from apps.automation.models import MonthlyPack, Notice
from apps.automation.notices import notify
from apps.billing.models import Bill
from apps.chamas.models import Membership
from apps.contributions import services as contrib
from apps.disputes.models import Dispute
from apps.documents.models import IssuedDocument
from apps.meetings.models import Meeting
from apps.payouts import services as payouts
from apps.payouts.models import UnapprovedDebit
from apps.reconciliation import services as recon
from apps.rules.templates import template_for
from apps.votes import services as votes

from .phase1 import active_chama, credit, make_user
from .test_phase2_money_out import _statement

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


@pytest.fixture
def money_out(settings, sms_outbox):
    """Active, money out on, KES 100,000 in the bank (reconciled), and a member, Mary."""
    settings.BANK_STATEMENT_SENDERS = {"equity": ["equitybank.example"]}
    c, (chair, treasurer, secretary) = active_chama(since=TODAY)
    mary = Membership.objects.create(chama=c, user=make_user("+254799000444", "Mary"),
                                     joined_on=TODAY, member_code=f"{c.member_code_prefix}-004")
    _statement(c, [(TODAY - timedelta(days=10), "OPENING DEPOSIT", 0, 10_000_000)], 0)
    for official in (chair, treasurer):
        ch = payouts.start_enable(c, official)
        payouts.enable(c, official, challenge_id=ch.id, sms_code=_code(sms_outbox))
    c.refresh_from_db()
    return c, chair, treasurer, secretary, mary


def _notices(member, prefix):
    return list(Notice.objects.filter(user=member.user, key__startswith=prefix))


# --- Notices -------------------------------------------------------------------------------

def test_a_notice_goes_once_and_by_sms_only_to_people_away_from_the_app(sms_outbox):
    away = make_user("+254799000501", "Away")
    here = make_user("+254799000502", "Here")
    here.last_app_at = timezone.now()
    here.save()
    text = {"en": "Hello", "sw": "Habari"}
    assert notify(away, key="k", kind="test", text=text) is not None
    assert notify(away, key="k", kind="test", text=text) is None  # once per key
    notify(here, key="k", kind="test", text=text)
    assert [to for to, _ in sms_outbox] == [away.phone]
    assert Notice.objects.get(user=away).sms_sent
    assert not Notice.objects.get(user=here).sms_sent
    notify(here, key="k2", kind="test", text=text, sms="always")
    assert sms_outbox[-1][0] == here.phone


def test_notices_through_the_api(api, sms_outbox):
    u = make_user("+254799000503", "Wanjiru")
    u.language = "sw"
    u.save()
    notify(u, key="a", kind="test", text={"en": "Hello", "sw": "Habari"})
    api.force_authenticate(u)
    r = api.get("/api/v1/me/notices/")
    assert r.status_code == 200 and r.data["unread"] == 1
    assert r.data["notices"][0]["text"] == "Habari"
    assert api.post("/api/v1/me/notices/read/", {}, format="json").data["marked"] == 1
    assert api.get("/api/v1/me/notices/").data["unread"] == 0


# --- Chasing ------------------------------------------------------------------------------

def test_officials_who_have_not_answered_are_reminded_before_an_approval_runs_out(
        money_out, sms_outbox, django_capture_on_commit_callbacks):
    c, chair, treasurer, secretary, mary = money_out
    p = payouts.propose(c, chair, kind="payout", amount_cents=1_000_000, payee=mary,
                        purpose="Welfare")
    ch = payouts.start_approve(p, secretary)
    payouts.approve(p, secretary, challenge_id=ch.id, sms_code=_code(sms_outbox))
    soon = p.deadline_at - timedelta(hours=3)
    assert chasers.approvals_soon(soon) == 1  # the treasurer; the chair proposed it
    [n] = _notices(treasurer, "approve-soon")
    assert n.sms_sent and "Approve or decline paying Mary KES 10,000" in n.text
    assert not _notices(secretary, "approve-soon") and not _notices(chair, "approve-soon")
    assert chasers.approvals_soon(soon) == 0  # never twice


def test_members_who_have_not_voted_hear_before_the_vote_closes(
        sms_outbox, django_capture_on_commit_callbacks):
    c, ms = active_chama(since=TODAY, members=6)
    with django_capture_on_commit_callbacks(execute=True):
        v = votes.open_vote(c, subject="test:1", question="Buy chairs?",
                            rules=template_for("merry_go_round"),
                            closes_at=timezone.now() + timedelta(days=2))
    ch = votes.start_sms_ballot(v, ms[0], "yes")
    votes.cast_sms(v, ms[0], choice="yes", challenge_id=ch.id, sms_code=_code(sms_outbox))
    sent = chasers.votes_soon(v.closes_at - timedelta(hours=2))
    assert sent == 5
    assert not _notices(ms[0], "vote-soon")
    # Members without a phone key get their reply number again by SMS.
    assert re.search(r"Reply YES \d{4} or NO \d{4}", sms_outbox[-1][1])


def test_cash_not_banked_after_a_week_tells_every_member(sms_outbox):
    c, (chair, treasurer, secretary) = active_chama(since=TODAY - timedelta(days=40))
    r = contrib.record_cash(c, treasurer, secretary, amount_cents=150_000,
                            received_on=TODAY - timedelta(days=8))
    ch = contrib.start_confirm_cash(r, secretary)
    contrib.confirm_cash(r, secretary, challenge_id=ch.id, sms_code=_code(sms_outbox))
    assert chasers.cash_unbanked(TODAY) == 3
    [n] = _notices(treasurer, "cash-unbanked")
    assert n.urgent and n.sms_sent and "still not in the bank after 7 days" in n.text


def test_cash_the_member_has_not_confirmed_reminds_them(sms_outbox):
    c, (chair, treasurer, secretary) = active_chama(since=TODAY)
    r = contrib.record_cash(c, treasurer, secretary, amount_cents=50_000, received_on=TODAY)
    assert chasers.cash_unconfirmed(r.created_at + timedelta(days=3)) == 1
    assert "confirm it" in _notices(secretary, "cash-confirm")[0].text


def test_unwritten_minutes_remind_the_secretary(sms_outbox):
    c, (chair, treasurer, secretary) = active_chama(since=TODAY)
    mt = Meeting.objects.create(chama=c, scheduled_by=secretary, title="AGM",
                                starts_at=timezone.now() - timedelta(days=4),
                                state=Meeting.State.ATTENDANCE_CLOSED)
    assert chasers.minutes_due(timezone.now()) == 1
    assert _notices(secretary, f"minutes-due:{mt.id}")
    assert not _notices(chair, "minutes-due")  # the chama has a secretary


def test_a_withdrawal_nobody_explains_for_72_hours_becomes_a_dispute(
        money_out, sms_outbox, django_capture_on_commit_callbacks):
    c, chair, treasurer, secretary, mary = money_out
    with django_capture_on_commit_callbacks(execute=True):
        _statement(c, [(TODAY - timedelta(days=1), "CASH WITHDRAWAL", 4_500_000, 0)],
                   10_000_000)
    d = UnapprovedDebit.objects.get(chama=c)
    later = d.created_at + timedelta(hours=73)
    with django_capture_on_commit_callbacks(execute=True):
        assert chasers.debits_to_disputes(later) == 1
    dispute = Dispute.objects.get(chama=c)
    assert dispute.automatic and dispute.raised_by is None
    assert dispute.subject_type == "payment" and dispute.subject_id == d.bank_txn_id
    assert set(dispute.involved) == {str(chair.id), str(treasurer.id)}  # the signatories
    assert "JiraniChama opened this dispute automatically" in dispute.text
    assert chasers.debits_to_disputes(later) == 0


# --- Payment reminders ------------------------------------------------------------------------

def test_reminders_before_on_and_after_the_due_date(sms_outbox):
    since = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=1)  # last month
    c, (chair, treasurer, secretary) = active_chama(since=since)
    period = f"{TODAY.year}-{TODAY.month:02d}"
    due = date(TODAY.year, TODAY.month, 5)  # merry-go-round: due on the 5th, 2 days' grace
    # Everyone has paid last month; the chair has paid this month too.
    for m in (chair, treasurer, secretary):
        credit(c, amount_cents=100_000, value_date=since, narration=m.member_code)
    for txn in c.bank_transactions.all():
        recon.match_credit(txn)
    credit(c, amount_cents=100_000, value_date=due, narration=chair.member_code)
    recon.match_credit(c.bank_transactions.filter(state="unmatched").get())
    assert reminders.payment_reminders(due - timedelta(days=3)) == 2
    [n] = _notices(treasurer, f"due-soon:{period}")
    assert n.text.startswith("Reminder: KES 1,000 is due on")
    assert "Deposit at Equity Bank, account ending 9012" in n.text
    assert treasurer.member_code in n.text
    assert not _notices(chair, "due-soon")
    assert reminders.payment_reminders(due - timedelta(days=3)) == 0  # once
    assert reminders.payment_reminders(due) == 2
    assert reminders.payment_reminders(due + timedelta(days=1)) == 0  # inside the grace
    assert reminders.payment_reminders(due + timedelta(days=3)) == 2
    assert "is late" in _notices(secretary, f"due-late:{period}")[0].text


def test_what_do_i_owe_and_how_to_pay(api):
    c, (chair, treasurer, secretary) = active_chama(since=TODAY - timedelta(days=70))
    api.force_authenticate(treasurer.user)
    r = api.get(f"/api/v1/chamas/{c.id}/pay/")
    assert r.status_code == 200
    assert r.data["owed"]["total_cents"] > 0
    assert r.data["how"]["member_code"] == treasurer.member_code
    assert r.data["how"]["pay_now"] is False


# --- The monthly pack -------------------------------------------------------------------------

def test_the_monthly_pack_gives_every_member_their_statement_once(sms_outbox):
    since = TODAY - timedelta(days=70)
    c, (chair, treasurer, secretary) = active_chama(since=since)
    credit(c, amount_cents=100_000, value_date=since, narration=secretary.member_code)
    recon.match_credit(c.bank_transactions.get())
    first = TODAY.replace(day=1)
    last_month = first - timedelta(days=1)
    period = f"{last_month.year}-{last_month.month:02d}"
    pack = monthly.pack(c, period)
    assert pack is not None and pack.members == 3
    assert IssuedDocument.objects.filter(chama=c, kind="member_statement").count() == 3
    assert IssuedDocument.objects.filter(chama=c, kind="chama_statement").count() == 1
    [n] = _notices(secretary, f"monthly:{period}")
    assert "Savings KES 1,000" in n.text and n.data["screen"] == "document"
    assert _notices(chair, f"monthly-report:{period}")
    assert monthly.pack(c, period) is None  # once a month
    assert MonthlyPack.objects.filter(chama=c).count() == 1


def test_the_treasurer_proposes_the_bill_with_one_tap(money_out, settings, sms_outbox, api):
    settings.BILLING_PAYBILL = "555111"
    c, chair, treasurer, secretary, mary = money_out
    period = f"{TODAY.year}-{TODAY.month:02d}"
    bill = Bill.objects.create(chama=c, period=period, currency="KES", active_members=8,
                               price_per_member_cents=1500, members_cents=12_000,
                               total_cents=12_000, state=Bill.State.OPEN, due_on=TODAY)
    monthly._bill_notice(c, period, [chair, treasurer, secretary])
    [n] = _notices(treasurer, "bill:")
    assert "Paybill 555111" in n.text and n.data == {"screen": "bill", "id": str(bill.id)}
    api.force_authenticate(treasurer.user)
    r = api.post(f"/api/v1/bills/{bill.id}/propose/")
    assert r.status_code == 201
    p = payouts.Proposal.objects.get(pk=r.data["proposal_id"])
    assert p.kind == "withdrawal" and p.payee_name == "JiraniChama"
    assert p.amount_cents == 12_000 and "555111" in p.payee_account
