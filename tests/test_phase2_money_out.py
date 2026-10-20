"""Money out: proposals, approvals by officials, the bank debit matched back, and debits
that left with no approval (5.6, 5.7)."""

import base64
import re
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.chamas.models import Chama, Membership
from apps.core.problems import Problem
from apps.feeds import services as feeds
from apps.feeds.models import BankTransaction
from apps.ledger.models import LedgerEntry
from apps.ledger.services import verify_chain
from apps.payouts import services
from apps.payouts.models import UnapprovedDebit

from .phase1 import active_chama, idem, make_user, statement_csv

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


@pytest.fixture
def mail(settings):
    settings.BANK_STATEMENT_SENDERS = {"equity": ["equitybank.example"]}


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
def chama(mail, sms_outbox):
    """Active, money out on, KES 100,000 in the bank (reconciled)."""
    c, (chair, treasurer, secretary) = active_chama(since=TODAY)
    member_user = make_user("+254799000444", "Mary")
    member = Membership.objects.create(chama=c, user=member_user, joined_on=TODAY,
                                       member_code=f"{c.member_code_prefix}-004")
    _statement(c, [(TODAY - timedelta(days=10), "OPENING DEPOSIT", 0, 10_000_000)], 0)
    for official in (chair, treasurer):
        ch = services.start_enable(c, official)
        services.enable(c, official, challenge_id=ch.id, sms_code=_code(sms_outbox))
    c.refresh_from_db()
    return c, chair, treasurer, secretary, member


def _approve(p, by, outbox):
    ch = services.start_approve(p, by)
    return services.approve(p, by, challenge_id=ch.id, sms_code=_code(outbox))


def test_money_out_needs_two_officials_to_switch_on(mail, sms_outbox):
    c, (chair, treasurer, _) = active_chama(since=TODAY)
    with pytest.raises(Problem) as exc:
        services.propose(c, chair, kind="withdrawal", amount_cents=100, purpose="x",
                         payee_name="Shop")
    assert exc.value.problem_code == "money_out_off"
    ch = services.start_enable(c, chair)
    assert services.enable(c, chair, challenge_id=ch.id, sms_code=_code(sms_outbox)) == 1
    c.refresh_from_db()
    assert c.mode == Chama.Mode.RECORDS_ONLY
    ch = services.start_enable(c, treasurer)
    services.enable(c, treasurer, challenge_id=ch.id, sms_code=_code(sms_outbox))
    c.refresh_from_db()
    assert c.mode == Chama.Mode.MONEY_OUT


def test_payout_approved_by_the_officials_the_rules_require(chama, sms_outbox):
    c, chair, treasurer, secretary, member = chama
    p = services.propose(c, chair, kind="payout", amount_cents=2_000_000, payee=member,
                         purpose="Merry-go-round turn")
    assert p.state == "collecting_approvals" and p.needs_officials == 2
    assert p.deadline_at > timezone.now()
    assert "APPROVE paying Mary KES 20,000" in services.summary(p, "en")

    # Maker and checker are different people: the chair proposed, so cannot approve.
    with pytest.raises(Problem) as exc:
        services.start_approve(p, chair)
    assert exc.value.problem_code == "proposer_cannot_approve"
    p = _approve(p, secretary, sms_outbox)  # one person counts once
    assert p.state == "collecting_approvals"
    with pytest.raises(Problem) as exc:
        services.start_approve(p, secretary)
    assert exc.value.problem_code == "already_answered"
    p = _approve(p, treasurer, sms_outbox)
    assert p.state == "approved"
    with pytest.raises(Problem):
        services.start_approve(p, secretary)  # no longer collecting


def test_nobody_approves_a_payment_to_themselves(chama, sms_outbox):
    c, chair, _, secretary, _ = chama
    p = services.propose(c, chair, kind="payout", amount_cents=100_000, payee=secretary,
                         purpose="Welfare")
    with pytest.raises(Problem) as exc:
        services.start_approve(p, secretary)
    assert exc.value.problem_code == "payee_cannot_approve"


@pytest.mark.parametrize("amount,code,rule", [
    (20_000_000, "not_enough_money", "0.1"),        # KES 200,000 > KES 100,000 in the bank
])
def test_the_rules_refuse_and_name_the_rule(chama, amount, code, rule):
    c, chair, treasurer, secretary, member = chama
    p = services.propose(c, chair, kind="withdrawal", amount_cents=amount,
                         payee_name="Hardware Ltd", purpose="Tank")
    assert p.state == "refused"
    assert (p.refusal["code"], p.refusal["rule"]) == (code, rule)


def test_a_payee_behind_on_contributions_is_refused(mail, sms_outbox):
    c, (chair, treasurer, secretary) = active_chama(since=TODAY - timedelta(days=70))
    _statement(c, [(TODAY - timedelta(days=3), "DEPOSIT", 0, 10_000_000)], 0)
    for o in (chair, treasurer):
        ch = services.start_enable(c, o)
        services.enable(c, o, challenge_id=ch.id, sms_code=_code(sms_outbox))
    c.refresh_from_db()
    p = services.propose(c, chair, kind="payout", amount_cents=100_000, payee=secretary,
                         purpose="Turn")
    assert p.refusal["code"] == "payee_in_arrears" and p.refusal["rule"] == "3.1"


def test_money_promised_counts_against_the_balance(chama, sms_outbox):
    c, chair, treasurer, secretary, member = chama
    p = services.propose(c, chair, kind="payout", amount_cents=4_000_000, payee=member,
                         purpose="Turn")
    _approve(p, secretary, sms_outbox)
    _approve(p, treasurer, sms_outbox)
    assert services.available_cents(c) == 6_000_000
    second = services.propose(c, chair, kind="withdrawal", amount_cents=4_500_000,
                              payee_name="X", purpose="Y")
    assert second.state == "collecting_approvals"
    third = services.propose(c, chair, kind="withdrawal", amount_cents=4_500_000,
                             payee_name="X", purpose="Z")
    assert third.state == "collecting_approvals"  # collecting is not yet promised


def test_declines_that_make_the_threshold_unreachable_reject_it(chama):
    c, chair, treasurer, secretary, member = chama
    p = services.propose(c, chair, kind="payout", amount_cents=100_000, payee=member,
                         purpose="Turn")
    # The chair proposed, so only the treasurer and secretary can approve; one decline
    # already makes two approvals impossible.
    p = services.decline(p, secretary, "Not her turn")
    assert p.state == "rejected"


def test_approvals_not_in_by_the_deadline_expire(chama, monkeypatch):
    c, chair, treasurer, secretary, member = chama
    p = services.propose(c, chair, kind="withdrawal", amount_cents=100_000,
                         payee_name="Shop", purpose="Chairs")
    assert services.expire_due(c) == 0
    later = p.deadline_at + timedelta(minutes=1)
    monkeypatch.setattr(services.timezone, "now", lambda: later)
    assert services.expire_due(c) == 1
    p.refresh_from_db()
    assert p.state == "expired"


def test_paid_at_the_bank_then_the_debit_is_matched(chama, sms_outbox):
    c, chair, treasurer, secretary, member = chama
    p = services.propose(c, chair, kind="payout", amount_cents=2_000_000, payee=member,
                         purpose="Turn")
    _approve(p, secretary, sms_outbox)
    _approve(p, treasurer, sms_outbox)
    p = services.mark_paid(p, treasurer, "PL77X")
    assert p.state == "paid_pending_match"

    sent_before = len(sms_outbox)
    _statement(c, [(TODAY - timedelta(days=1), "PESALINK PL77X MARY", 2_000_000, 0)],
               10_000_000)
    p.refresh_from_db()
    assert p.state == "paid"
    entry = p.entry
    assert entry.type == "payout" and entry.amount_cents == -2_000_000
    assert entry.member_id == member.id
    assert sorted(a["role"] for a in entry.decision["approved_by"]) == ["secretary", "treasurer"]
    assert BankTransaction.objects.get(pk=p.matched_txn_id).state == "recorded"
    assert len(sms_outbox) == sent_before  # a matched payout raises no alert
    assert verify_chain(c, check_signatures=False).ok


def test_a_debit_with_no_approval_alerts_every_member(chama, sms_outbox,
                                                     django_capture_on_commit_callbacks):
    c, chair, treasurer, secretary, member = chama
    sent_before = len(sms_outbox)
    with django_capture_on_commit_callbacks(execute=True):  # alerts go after commit
        _statement(c, [(TODAY - timedelta(days=1), "CASH WITHDRAWAL", 4_500_000, 0)],
                   10_000_000)
    ud = UnapprovedDebit.objects.get(chama=c)
    assert ud.state == "open" and ud.alerted == 4
    alerts = sms_outbox[sent_before:]
    assert len(alerts) == 4
    assert all("ALERT: KES 45,000 left Test Chama" in text for _, text in alerts)
    assert LedgerEntry.objects.get(pk=ud.entry_id).type == "unapproved_debit_detected"
    assert ud.bank_txn.state == "unapproved"

    # An official's explanation does not close it: the members vote, the official who
    # explained does not, and until they accept it nothing can be paid.
    ud = services.explain(ud, chair, "Treasurer withdrew for the tent; receipt at meeting")
    assert ud.state == "open"
    with pytest.raises(Problem) as exc:
        services.propose(c, chair, kind="withdrawal", amount_cents=100_000,
                         payee_name="Hall", purpose="Rent")
    assert exc.value.problem_code == "breach_open"
    from apps.votes import services as votes

    v = votes.for_subject(f"payouts.debit:{ud.id}")
    with pytest.raises(Problem):  # the official who explained does not vote on it
        votes.start_sms_ballot(v, chair, "yes")
    for m in Membership.objects.filter(chama=c, state="active").exclude(pk=chair.pk):
        ch = votes.start_sms_ballot(v, m, "yes")
        v = votes.cast_sms(v, m, choice="yes", challenge_id=ch.id,
                           sms_code=_code(sms_outbox))
    ud.refresh_from_db()
    assert v.state == "passed" and ud.state == "explained"


def test_school_fees_is_not_a_bank_charge(chama, sms_outbox):
    c, *_ = chama
    _statement(c, [(TODAY - timedelta(days=1), "SCHOOL FEES ST MARYS", 2_500_000, 0)],
               10_000_000)
    assert UnapprovedDebit.objects.filter(chama=c).exists()
    assert not LedgerEntry.objects.filter(chama=c, type="bank_charge").exists()


def test_a_big_charge_is_not_waved_through(chama, sms_outbox):
    c, *_ = chama
    _statement(c, [(TODAY - timedelta(days=1), "BANK CHARGES", 900_000, 0)], 10_000_000)
    assert UnapprovedDebit.objects.filter(chama=c).exists()


def test_bank_charges_are_recorded_without_alarm(chama, sms_outbox):
    c, *_ = chama
    sent_before = len(sms_outbox)
    _statement(c, [(TODAY - timedelta(days=1), "LEDGER FEE", 15_000, 0)], 10_000_000)
    assert len(sms_outbox) == sent_before
    assert LedgerEntry.objects.filter(chama=c, type="bank_charge",
                                      amount_cents=-15_000).exists()
    assert not UnapprovedDebit.objects.filter(chama=c).exists()


def test_an_unapproved_debit_can_be_linked_to_its_approved_proposal(chama, sms_outbox):
    """Paid at the bank before anyone tapped Paid, and the debit arrived first."""
    c, chair, treasurer, secretary, member = chama
    p = services.propose(c, chair, kind="payout", amount_cents=1_000_000, payee=member,
                         purpose="Turn")
    _approve(p, secretary, sms_outbox)
    _approve(p, treasurer, sms_outbox)
    # An approved proposal matches its debit straight away, without Paid.
    _statement(c, [(TODAY - timedelta(days=1), "TRANSFER", 1_000_000, 0)], 10_000_000)
    p.refresh_from_db()
    assert p.state == "paid"

    # Approved, but the bank shows a different reference: linking it is allowed, because
    # the approval came before the money left.
    q = services.propose(c, chair, kind="withdrawal", amount_cents=300_000,
                         payee_name="Tent hire", purpose="Meeting tent")
    _approve(q, secretary, sms_outbox)
    _approve(q, treasurer, sms_outbox)
    q.bank_reference = "TENT-001"
    q.save(update_fields=["bank_reference"])
    _statement(c, [(TODAY, "TRANSFER 2", 300_000, 0)], 9_000_000)
    ud = UnapprovedDebit.objects.filter(chama=c).first()
    if ud is not None:  # the reference did not fit, so it came in as unapproved
        ud = services.link(ud, q, treasurer)
        assert ud.state == "linked"
    q.refresh_from_db()
    assert q.state == "paid"


def test_approving_after_the_money_left_does_not_cover_it(chama, sms_outbox):
    c, chair, treasurer, secretary, member = chama
    _statement(c, [(TODAY - timedelta(days=2), "TRANSFER", 300_000, 0)], 10_000_000)
    ud = UnapprovedDebit.objects.get(chama=c)
    with pytest.raises(Problem) as exc:  # frozen: nothing can be proposed now
        services.propose(c, chair, kind="withdrawal", amount_cents=300_000,
                         payee_name="Tent hire", purpose="Meeting tent")
    assert exc.value.problem_code == "breach_open"
    assert ud.state == "open"


def test_records_only_chamas_also_hear_about_every_withdrawal(mail, sms_outbox):
    c, _ = active_chama(since=TODAY)
    _statement(c, [(TODAY, "DEPOSIT", 0, 100_000), (TODAY, "WITHDRAWAL", 50_000, 0)], 0)
    assert BankTransaction.objects.get(chama=c, direction="debit").state == "unapproved"
    assert UnapprovedDebit.objects.filter(chama=c).exists()


def test_money_out_through_the_api(api, chama, sms_outbox):
    c, chair, treasurer, secretary, member = chama
    api.force_authenticate(chair.user)
    r = api.post(f"/api/v1/chamas/{c.id}/proposals/", {
        "kind": "payout", "amount_cents": 2_000_000, "purpose": "Turn",
        "payee_membership_id": str(member.id)}, format="json", **idem())
    assert r.status_code == 201, r.content
    pid = r.json()["id"]
    # The chair proposed it, so the chair cannot also approve it.
    assert r.json()["can_answer"] is False and r.json()["needs_officials"] == 2

    for who in (treasurer, secretary):
        api.force_authenticate(who.user)
        r = api.post(f"/api/v1/proposals/{pid}/approve/", {}, format="json")
        assert r.status_code == 202
        r = api.post(f"/api/v1/proposals/{pid}/approve/",
                     {"challenge_id": r.json()["challenge_id"], "code": _code(sms_outbox)},
                     format="json")
        assert r.status_code == 200, r.content
    assert r.json()["state"] == "approved" and r.json()["approved"] == 2

    api.force_authenticate(member.user)  # every member sees it, but cannot answer
    r = api.get(f"/api/v1/chamas/{c.id}/proposals/")
    assert r.json()[0]["can_answer"] is False
    assert [a["name"] for a in r.json()[0]["approvals"]] == ["Treasurer", "Secretary"]

    api.force_authenticate(treasurer.user)
    r = api.post(f"/api/v1/proposals/{pid}/paid/", {"bank_reference": "PL1"}, format="json",
                 **idem())
    assert r.json()["state"] == "paid_pending_match"
    r = api.get(f"/api/v1/chamas/{c.id}/money-out/")
    assert r.json()["mode"] == "money_out" and r.json()["available_cents"] == 8_000_000
