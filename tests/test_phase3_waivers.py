"""Fine waivers (5.9 steps 5 and 6)."""

import copy
import re
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.problems import Problem
from apps.fines import waivers
from apps.fines.models import Fine, Waiver
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


def _fine(m, amount=20_000, ref="3.2", period="2026-01"):
    entry = ledger.append(chama=m.chama, type="fine_raised", source="test", member=m,
                          amount_cents=amount, period=period, rule_refs=[ref],
                          decision={"cause": "late_contribution"})
    return Fine.objects.create(chama=m.chama, membership=m, rule_ref=ref, period=period,
                               amount_cents=amount, raised_entry=entry)


def _approve(w, by, outbox):
    ch = waivers.start_approve(w, by)
    assert "WAIVE" in outbox[-1][1]
    return waivers.approve(w, by, challenge_id=ch.id, sms_code=_code(outbox))


def test_two_officials_waive_and_everyone_sees_the_reason(sms_outbox):
    c, (chair, treasurer, secretary, member) = active_chama(since=TODAY, members=4)
    fine = _fine(member)
    with pytest.raises(Problem) as e:
        waivers.request(fine, chair, reason="x")
    assert e.value.problem_code == "not_allowed"  # only for your own fine
    w = waivers.request(fine, member, reason="I was in hospital")
    with pytest.raises(Problem) as e:
        waivers.start_approve(w, member)
    assert e.value.problem_code == "own_fine"
    _approve(w, chair, sms_outbox)
    w.refresh_from_db()
    assert w.state == "requested"
    w = _approve(w, treasurer, sms_outbox)
    assert w.state == "waived"
    fine.refresh_from_db()
    assert fine.state == "waived" and fine.outstanding_cents == 0
    entry = LedgerEntry.objects.get(type="fine_waived", source_id=w.id)
    assert entry.amount_cents == 20_000 and entry.decision["reason"] == "I was in hospital"
    assert set(entry.decision["approved_by"]) == {chair.member_code, treasurer.member_code}


def test_an_officials_own_fine_is_decided_by_the_others(sms_outbox):
    c, (chair, treasurer, secretary) = active_chama(since=TODAY)
    fine = _fine(chair)
    w = waivers.request(fine, chair, reason="Bank was closed")
    with pytest.raises(Problem) as e:
        waivers.start_approve(w, chair)
    assert e.value.problem_code == "own_fine"
    _approve(w, treasurer, sms_outbox)
    assert _approve(w, secretary, sms_outbox).state == "waived"


def test_declines_refuse_it_and_a_second_request_waits(sms_outbox):
    c, (chair, treasurer, secretary, member) = _four()
    fine = _fine(member)
    w = waivers.request(fine, member, reason="Forgot")
    with pytest.raises(Problem) as e:
        waivers.request(fine, member, reason="Again")
    assert e.value.problem_code == "already_sent"
    waivers.decline(w, chair, reason="No reason given")
    w.refresh_from_db()
    assert w.state == "requested"  # two officials can still approve
    w = waivers.decline(w, treasurer)
    assert w.state == "refused"
    fine.refresh_from_db()
    assert fine.state == "raised"


def _four():
    return active_chama(since=TODAY, members=4)


def test_a_member_vote_when_the_rules_want_one(sms_outbox):
    rules = copy.deepcopy(template_for("merry_go_round"))
    rules["waivers"] = {"officials": 1, "member_vote": True}
    c, (chair, treasurer, secretary, member) = active_chama(since=TODAY, members=4,
                                                            rules=rules)
    w = waivers.request(_fine(member), member, reason="Sick")
    v = votes.for_subject(f"fines.waiver:{w.id}")
    assert v is not None and member.id not in [m for m in v.eligible_ids]
    _approve(w, chair, sms_outbox)
    w.refresh_from_db()
    assert w.state == "requested"  # the vote has not passed yet
    for m in (chair, treasurer, secretary):
        ch = votes.start_sms_ballot(v, m, "yes")
        v = votes.cast_sms(v, m, choice="yes", challenge_id=ch.id, sms_code=_code(sms_outbox))
    w.refresh_from_db()
    assert v.state == "passed" and w.state == "waived"


def test_paid_meanwhile_and_expired(monkeypatch, sms_outbox):
    c, (chair, treasurer, secretary, member) = _four()
    fine = _fine(member)
    w = waivers.request(fine, member, reason="x")
    Fine.objects.filter(pk=fine.pk).update(paid_cents=20_000, state="paid")
    _approve(w, chair, sms_outbox)
    w = _approve(w, treasurer, sms_outbox)
    assert w.state == "refused" and "paid meanwhile" in w.state_reason

    w2 = waivers.request(_fine(member, period="2026-02"), member, reason="y")
    later = timezone.now() + timedelta(hours=73)
    monkeypatch.setattr(waivers.timezone, "now", lambda: later)
    assert waivers.expire_due() == 1
    w2.refresh_from_db()
    assert w2.state == "expired"


def test_the_rules_cannot_ask_more_officials_than_can_decide():
    r = copy.deepcopy(template_for("merry_go_round"))
    r["waivers"] = {"officials": 3, "member_vote": False}
    codes = {e.code for e in validation.check(r)}
    assert "waivers_too_many_officials" in codes


def test_fines_and_waivers_through_the_api(api, sms_outbox):
    c, (chair, treasurer, secretary, member) = _four()
    fine = _fine(member)
    api.force_authenticate(member.user)
    r = api.post(f"/api/v1/fines/{fine.id}/waiver/", {"reason": "Hospital"}, format="json")
    assert r.status_code == 201, r.content
    wid = r.json()["waiver"]["id"]
    api.force_authenticate(chair.user)
    listed = api.get(f"/api/v1/chamas/{c.id}/fines/").json()
    assert listed[0]["waiver"]["i_can_decide"] is True and listed[0]["mine"] is False
    r = api.post(f"/api/v1/waivers/{wid}/approve/", {}, format="json")
    assert r.status_code == 202
    r = api.post(f"/api/v1/waivers/{wid}/approve/",
                 {"challenge_id": r.json()["challenge_id"], "sms_code": _code(sms_outbox)},
                 format="json")
    assert r.json()["waiver"]["approvals"] == 1
    api.force_authenticate(treasurer.user)
    r = api.post(f"/api/v1/waivers/{wid}/decline/", {"reason": "No"}, format="json")
    assert r.json()["waiver"]["state"] == "requested"
    assert Waiver.objects.get(pk=wid).decisions.count() == 2
