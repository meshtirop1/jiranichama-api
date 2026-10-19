"""Disputes (5.14)."""

import copy
import re
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.contributions.projections import position
from apps.core.problems import Problem
from apps.disputes import services as disputes
from apps.disputes.models import Dispute
from apps.ledger import services as ledger
from apps.ledger.models import LedgerEntry
from apps.rules import validation
from apps.rules.templates import template_for
from apps.votes import services as votes

from .phase1 import active_chama

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


def _five(rules=None):
    return active_chama(since=TODAY, members=5, rules=rules)


def _entry(m, amount=100_000):
    return ledger.append(chama=m.chama, type="contribution", source="test", member=m,
                         amount_cents=amount, period="2026-01")


def _past_response(monkeypatch, d):
    later = d.respond_until + timedelta(minutes=1)
    monkeypatch.setattr(disputes.timezone, "now", lambda: later)


def _agree(d, by, outbox):
    ch = disputes.start_agree(d, by)
    assert "AGREE the dispute outcome" in outbox[-1][1]
    return disputes.agree(d, by, challenge_id=ch.id, sms_code=_code(outbox))


def test_raised_shown_and_the_people_involved_told(sms_outbox,
                                                   django_capture_on_commit_callbacks):
    c, (chair, treasurer, secretary, mary, john) = _five()
    e = _entry(mary)
    with django_capture_on_commit_callbacks(execute=True):
        d = disputes.raise_dispute(c, john, subject_type="entry", subject_id=e.id,
                                   reason="wrong_amount", text="Mary paid 500, not 1,000")
    assert d.involved == [str(mary.id)] and d.state == "awaiting_response"
    assert "raised a problem that involves you" in sms_outbox[-1][1]
    with pytest.raises(Problem) as ex:
        disputes.raise_dispute(c, john, subject_type="entry", subject_id=chair.id,
                               reason="other", text="x")
    assert ex.value.problem_code == "subject_not_found"


def test_officials_decide_after_the_response_and_a_reversal_is_written(monkeypatch,
                                                                       sms_outbox):
    c, (chair, treasurer, secretary, mary, john) = _five()
    e = _entry(mary)
    d = disputes.raise_dispute(c, john, subject_type="entry", subject_id=e.id,
                               reason="not_mine", text="Recorded twice")
    with pytest.raises(Problem) as ex:
        disputes.propose_outcome(d, chair, outcome="reversal", text="Duplicate")
    assert ex.value.problem_code == "awaiting_response"
    disputes.respond(d, mary, text="Yes, it was recorded twice")  # all involved responded
    with pytest.raises(Problem) as ex:
        disputes.propose_outcome(d, mary, outcome="reversal", text="x")
    assert ex.value.problem_code == "not_allowed"  # involved people do not decide
    d = disputes.propose_outcome(d, chair, outcome="reversal", text="Duplicate entry")
    assert d.state == "deciding"
    d = _agree(d, treasurer, sms_outbox)
    assert d.state == "resolved"
    rev = LedgerEntry.objects.get(type="reversal", reverses=e)
    assert rev.amount_cents == -100_000 and rev.decision["dispute_id"] == str(d.id)
    assert d.entry.type == "dispute_resolved"
    assert set(d.entry.decision["agreed"]) == {chair.member_code, treasurer.member_code}


def test_a_correction_puts_savings_right_and_disagreement_reopens(monkeypatch, sms_outbox):
    c, (chair, treasurer, secretary, mary, john) = _five()
    d = disputes.raise_dispute(c, mary, subject_type="other", reason="wrong_amount",
                               text="My March cash was not recorded")
    assert set(d.involved) == {str(chair.id), str(treasurer.id), str(secretary.id)}
    # Every official is involved, so only a vote could settle this one.
    _past_response(monkeypatch, d)
    with pytest.raises(Problem) as ex:
        disputes.propose_outcome(d, chair, outcome="no_change", text="x")
    assert ex.value.problem_code == "not_allowed"

    e = _entry(mary)
    d = disputes.raise_dispute(c, john, subject_type="entry", subject_id=e.id,
                               reason="wrong_amount", text="Should be 1,500")
    _past_response(monkeypatch, d)
    d = disputes.propose_outcome(d, chair, outcome="correction", text="Short by 500",
                                 member_id=mary.id, amount_cents=50_000)
    d = disputes.disagree(d, treasurer, text="Receipt shows 1,000")
    assert d.state == "deciding"  # chair agreed; secretary may still agree
    d = disputes.disagree(d, secretary, text="Same")
    assert d.state == "awaiting_response" and d.outcome == ""
    d = disputes.propose_outcome(d, treasurer, outcome="correction", text="Short by 500",
                                 member_id=mary.id, amount_cents=50_000)
    assert d.round == 2
    before = position(mary, TODAY).credit_cents
    d = _agree(d, secretary, sms_outbox)
    assert d.state == "resolved"
    # The correction is the member's money, held as credit for her next months.
    assert position(mary, TODAY).credit_cents == before + 50_000


def test_a_disputed_payout_pauses(settings, sms_outbox):
    from apps.payouts.models import Proposal

    c, (chair, treasurer, secretary, mary, john) = _five()
    p = Proposal.objects.create(chama=c, kind="payout", amount_cents=100_000,
                                payee_membership=mary, payee_name="Mary", purpose="Turn",
                                proposed_by=chair, rule_version_id=c.rule_versions.first().id,
                                needs_officials=2, deadline_at=timezone.now()
                                + timedelta(hours=72))
    from apps.payouts import services as payouts

    disputes.raise_dispute(c, john, subject_type="proposal", subject_id=p.id,
                           reason="not_approved", text="Not her turn")
    with pytest.raises(Problem) as ex:
        payouts.start_approve(p, treasurer)
    assert ex.value.problem_code == "disputed"


def test_members_vote_when_the_rules_say(monkeypatch, sms_outbox):
    rules = copy.deepcopy(template_for("merry_go_round"))
    rules["disputes"]["decided_by"] = "vote"
    c, (chair, treasurer, secretary, mary, john) = _five(rules)
    d = disputes.raise_dispute(c, john, subject_type="entry", subject_id=_entry(mary).id,
                               reason="other", text="x")
    _past_response(monkeypatch, d)
    d = disputes.propose_outcome(d, chair, outcome="no_change", text="Records are right")
    monkeypatch.undo()
    v = votes.for_subject(f"disputes.dispute:{d.id}:1")
    for m in (chair, treasurer, secretary, mary, john):
        ch = votes.start_sms_ballot(v, m, "yes")
        v = votes.cast_sms(v, m, choice="yes", challenge_id=ch.id, sms_code=_code(sms_outbox))
    d.refresh_from_db()
    assert v.state == "passed" and d.state == "resolved" and d.outcome == "no_change"


def test_private_withdrawn_escalated_and_the_evidence_pack(monkeypatch):
    c, (chair, treasurer, secretary, mary, john) = _five()
    e = _entry(mary)
    d = disputes.raise_dispute(c, mary, subject_type="entry", subject_id=e.id,
                               reason="other", text="A personal matter",
                               officials_only=True)
    assert not disputes.can_see(d, john) and disputes.can_see(d, chair)
    assert d not in disputes.visible(c, john)
    pack = disputes.evidence(d)
    assert pack["entries"][0]["hash"] == e.hash and pack["entries"][0]["receipt"]
    with pytest.raises(Problem):
        disputes.withdraw(d, john)
    assert disputes.withdraw(d, mary).state == "withdrawn"
    d2 = disputes.raise_dispute(c, john, subject_type="entry", subject_id=e.id,
                                reason="other", text="x")
    assert disputes.escalate(d2, john, text="Small Claims Court").state == "escalated_outside"


def test_not_me_opens_a_dispute(settings):
    from apps.feeds.models import BankTransaction
    from apps.reconciliation import services as recon

    from .phase1 import credit

    c, (chair, treasurer, secretary, mary, john) = _five()
    txn = credit(c, amount_cents=100_000, value_date=TODAY, narration="CASH DEP")
    recon.assign(txn, mary, by=treasurer, reason="Looks like hers")
    recon.not_me(BankTransaction.objects.get(pk=txn.pk), mary, reason="I paid by M-Pesa")
    d = Dispute.objects.get(subject_type="payment", subject_id=txn.id)
    assert d.raised_by_id == mary.id and d.reason == "not_mine"
    assert str(treasurer.id) in d.involved


def test_rules_cannot_need_more_officials_than_can_decide():
    r = copy.deepcopy(template_for("merry_go_round"))
    r["disputes"]["officials"] = 3
    assert "disputes_too_many_officials" in {e.code for e in validation.check(r)}


def test_disputes_through_the_api(api, monkeypatch, sms_outbox):
    c, (chair, treasurer, secretary, mary, john) = _five()
    e = _entry(mary)
    api.force_authenticate(john.user)
    r = api.post(f"/api/v1/chamas/{c.id}/disputes/",
                 {"subject_type": "entry", "subject_id": str(e.id), "reason": "wrong_amount",
                  "text": "Wrong"}, format="json")
    assert r.status_code == 201, r.content
    did = r.json()["id"]
    api.force_authenticate(mary.user)
    r = api.post(f"/api/v1/disputes/{did}/respond/", {"text": "It is right"}, format="json")
    assert r.json()["messages"][0]["kind"] == "response"
    api.force_authenticate(chair.user)
    assert api.get(f"/api/v1/disputes/{did}/evidence/").json()["entries"][0]["seq"] == e.seq
    r = api.post(f"/api/v1/disputes/{did}/outcome/",
                 {"outcome": "no_change", "text": "The bank line shows 1,000"}, format="json")
    assert r.json()["state"] == "deciding" and r.json()["agreed"] == 1
    api.force_authenticate(treasurer.user)
    listed = api.get(f"/api/v1/chamas/{c.id}/disputes/").json()
    assert listed[0]["i_can_decide"] is True
    r = api.post(f"/api/v1/disputes/{did}/agree/", {}, format="json")
    assert r.status_code == 202
    r = api.post(f"/api/v1/disputes/{did}/agree/",
                 {"challenge_id": r.json()["challenge_id"], "sms_code": _code(sms_outbox)},
                 format="json")
    assert r.json()["state"] == "resolved" and r.json()["receipt"]
