"""Elections, acting officials and the bank mandate (5.12)."""

import hashlib
import re
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from django.utils import timezone

from apps.chamas.models import Membership
from apps.core.problems import Problem
from apps.devices.models import Device
from apps.elections import services as elections
from apps.ledger.models import LedgerEntry
from apps.votes import services as votes

from .phase1 import active_chama
from .test_phase2_device_keys import _spki

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


def _key(user):
    key = ec.generate_private_key(ec.SECP256R1())
    return Device.objects.create(user=user, public_key_der=_spki(key),
                                 fingerprint=hashlib.sha256(_spki(key)).hexdigest(),
                                 platform="android", security_level="tee")


def _everyone_votes(v, people, outbox, choice="yes"):
    for m in people:
        ch = votes.start_sms_ballot(v, m, choice)
        v = votes.cast_sms(v, m, choice=choice, challenge_id=ch.id, sms_code=_code(outbox))
    return v


def test_the_winner_takes_the_role_once_their_phone_key_is_active(sms_outbox,
                                                                  django_capture_on_commit_callbacks):
    c, ms = active_chama(since=TODAY, members=5)
    chair, treasurer, secretary, mary, john = ms
    with pytest.raises(Problem):
        elections.nominate(c, mary, role="treasurer", candidate_id=john.id)  # not an official
    e = elections.nominate(c, chair, role="treasurer", candidate_id=mary.id)
    assert e.outgoing_id == treasurer.id
    v = votes.for_subject(f"elections.election:{e.id}")
    _everyone_votes(v, ms, sms_outbox)
    e.refresh_from_db()
    assert e.state == "won"  # no phone key yet: the outgoing treasurer stays
    treasurer.refresh_from_db()
    assert treasurer.role == "treasurer"

    with django_capture_on_commit_callbacks(execute=True):
        _key(mary.user)
        assert elections.install_ready(c) == 1
    e.refresh_from_db()
    mary.refresh_from_db()
    treasurer.refresh_from_db()
    assert e.state == "in_office" and mary.role == "treasurer" and treasurer.role == "member"
    entry = e.role_entry
    assert entry.type == "role_changed" and entry.decision["outgoing"] == treasurer.member_code
    assert e.mandate_needed and "Update the signatories at the bank" in sms_outbox[-1][1]
    elections.confirm_mandate(e, chair)
    assert LedgerEntry.objects.filter(type="role_changed",
                                      decision__bank_mandate_updated=True).exists()


def test_a_lost_election_changes_nothing(sms_outbox):
    c, ms = active_chama(since=TODAY, members=5)
    chair, treasurer, secretary, mary, john = ms
    e = elections.nominate(c, chair, role="secretary", candidate_id=john.id)
    v = votes.for_subject(f"elections.election:{e.id}")
    _everyone_votes(v, ms, sms_outbox, choice="no")
    e.refresh_from_db()
    secretary.refresh_from_db()
    assert e.state == "lost" and secretary.role == "secretary"


def test_an_acting_official_starts_at_once_and_steps_down_if_not_confirmed(sms_outbox):
    c, ms = active_chama(since=TODAY, members=5)
    chair, treasurer, secretary, mary, john = ms
    with pytest.raises(Problem) as ex:
        elections.nominate(c, chair, role="treasurer", candidate_id=mary.id, acting=True)
    assert ex.value.problem_code == "role_filled"
    Membership.objects.filter(pk=treasurer.pk).update(state=Membership.State.DECEASED)
    e = elections.nominate(c, chair, role="treasurer", candidate_id=mary.id, acting=True)
    mary.refresh_from_db()
    assert e.state == "acting" and mary.role == "treasurer"
    v = votes.for_subject(f"elections.election:{e.id}")
    assert v.closes_at > timezone.now() + timedelta(days=29)
    _everyone_votes(v, [chair, secretary, mary, john], sms_outbox, choice="no")
    e.refresh_from_db()
    mary.refresh_from_db()
    assert e.state == "lost" and mary.role == "member"


def test_money_out_waits_for_an_election_when_too_few_officials_remain(settings, sms_outbox):
    from apps.payouts import services as payouts

    c, (chair, treasurer, secretary, mary) = active_chama(since=TODAY, members=4)
    Membership.objects.filter(pk__in=[treasurer.pk, secretary.pk]) \
        .update(state=Membership.State.EXITED)
    assert elections.needs_election(c)[0]["officials"] == 2
    tier, refusal, _ = payouts._rules_check(c, amount_cents=100_000, payee=mary, today=TODAY)
    assert refusal["code"] == "election_needed"


def test_elections_through_the_api(api, sms_outbox):
    c, (chair, treasurer, secretary, mary) = active_chama(since=TODAY, members=4)
    api.force_authenticate(chair.user)
    r = api.post(f"/api/v1/chamas/{c.id}/elections/",
                 {"role": "secretary", "candidate_id": str(mary.id)}, format="json")
    assert r.status_code == 201, r.content
    assert r.json()["state"] == "voting" and r.json()["outgoing"]
    body = api.get(f"/api/v1/chamas/{c.id}/elections/").json()
    assert body["elections"][0]["candidate_ready"] is False
    assert body["election_needed"] == []
