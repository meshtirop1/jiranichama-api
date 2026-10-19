"""Referrals to licensed partners, only with the chama's consent (4.10)."""

import re

import pytest
from django.utils import timezone

from apps.core.problems import Problem
from apps.ledger.models import LedgerEntry
from apps.referrals import services as referrals
from apps.referrals.models import Partner
from apps.votes import services as votes

from .phase1 import active_chama

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


def _partner():
    return Partner.objects.create(name="Umoja SACCO", regulator="sasra",
                                  licence_number="SASRA/123", product="group_loan")


def _vote_all(r, ms, outbox, choice):
    v = votes.for_subject(f"referrals.referral:{r.id}")
    for m in ms:
        ch = votes.start_sms_ballot(v, m, choice)
        v = votes.cast_sms(v, m, choice=choice, challenge_id=ch.id, sms_code=_code(outbox))
    r.refresh_from_db()
    return r


def test_nothing_is_shared_until_the_members_consent(sms_outbox):
    c, ms = active_chama(since=TODAY)
    chair = ms[0]
    r = referrals.propose(c, chair, partner_id=_partner().id, reason="We need a group loan")
    with pytest.raises(Problem) as e:
        referrals.handover(r)
    assert e.value.problem_code == "no_consent"
    r = _vote_all(r, ms, sms_outbox, "yes")
    assert r.state == "consented"
    entry = LedgerEntry.objects.get(type="referral_consent", source_id=r.id)
    assert entry.decision["licence_number"] == "SASRA/123"
    shared = referrals.handover(r)
    assert set(shared) == {"chama", "type", "members", "contact_name", "contact_phone",
                           "product"}
    assert referrals.mark_sent(r).state == "sent"


def test_a_no_vote_or_a_withdrawal_stops_it(sms_outbox):
    c, ms = active_chama(since=TODAY)
    chair, treasurer, secretary = ms
    partner = _partner()
    r = referrals.propose(c, chair, partner_id=partner.id, reason="x")
    with pytest.raises(Problem):
        referrals.propose(c, treasurer, partner_id=partner.id, reason="again")
    assert _vote_all(r, ms, sms_outbox, "no").state == "refused"
    r2 = referrals.propose(c, chair, partner_id=partner.id, reason="Try again")
    _vote_all(r2, ms, sms_outbox, "yes")
    referrals.withdraw(r2, secretary)
    r2.refresh_from_db()
    assert r2.state == "withdrawn"
    with pytest.raises(Problem):
        referrals.handover(r2)


def test_members_do_not_propose_and_the_api_lists_partners(api):
    c, (chair, treasurer, secretary, member) = active_chama(since=TODAY, members=4)
    p = _partner()
    with pytest.raises(Problem):
        referrals.propose(c, member, partner_id=p.id, reason="x")
    api.force_authenticate(member.user)
    assert api.get("/api/v1/partners/").json()[0]["regulator_name"].startswith("SACCO")
    api.force_authenticate(chair.user)
    r = api.post(f"/api/v1/chamas/{c.id}/referrals/",
                 {"partner_id": str(p.id), "reason": "Group account"}, format="json")
    assert r.status_code == 201 and r.json()["state"] == "voting"
