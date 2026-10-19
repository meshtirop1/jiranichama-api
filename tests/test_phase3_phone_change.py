"""Changing phones (5.12): hand-over, lost phones, vouching and cooling-off."""

import base64
import re
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.problems import Problem
from apps.devices import changes, keys
from apps.devices.models import Device
from apps.ledger.models import LedgerEntry
from apps.votes import services as votes

from .phase1 import active_chama
from .test_phase2_device_keys import _phone_key, _register, _sign, _spki

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


def test_a_members_new_phone_after_a_lost_one_waits_24_hours(api, monkeypatch, sms_outbox):
    c, (chair, treasurer, secretary, member) = active_chama(since=TODAY, members=4)
    r, _, _ = _register(api, member.user, sms_outbox)
    assert r.json()["state"] == "active" and r.json()["arrival"] == "first"
    r, _, _ = _register(api, member.user, sms_outbox)
    assert r.status_code == 201, r.content
    new = Device.objects.get(pk=r.json()["id"])
    assert new.state == "pending" and new.arrival == "lost" and not new.needs_officials
    assert Device.objects.filter(user=member.user, state="revoked").count() == 1
    with pytest.raises(Problem) as e:
        changes.require_can_sign(member.user)  # no SMS codes either: SIM swap guard
    assert e.value.problem_code == "device_pending"
    later = timezone.now() + timedelta(hours=25)
    monkeypatch.setattr(changes.timezone, "now", lambda: later)
    assert keys.active_device(member.user).pk == new.pk


def test_an_officials_new_phone_waits_for_the_other_officials(api, sms_outbox,
                                                              django_capture_on_commit_callbacks):
    c, (chair, treasurer, secretary, member) = active_chama(since=TODAY, members=4)
    _register(api, chair.user, sms_outbox)
    with django_capture_on_commit_callbacks(execute=True):
        r, _, _ = _register(api, chair.user, sms_outbox)
    new = Device.objects.get(pk=r.json()["id"])
    assert new.state == "pending" and new.needs_officials
    assert "set up a new phone after losing the old one" in sms_outbox[-1][1]

    api.force_authenticate(treasurer.user)
    waiting = api.get(f"/api/v1/chamas/{c.id}/new-phones/").json()
    assert [w["id"] for w in waiting] == [str(new.id)]
    r = api.post(f"/api/v1/devices/{new.id}/vouch/", {"chama_id": str(c.id)}, format="json")
    assert r.status_code == 202
    r = api.post(f"/api/v1/devices/{new.id}/vouch/",
                 {"chama_id": str(c.id), "challenge_id": r.json()["challenge_id"],
                  "code": _code(sms_outbox)}, format="json")
    assert r.json()["state"] == "pending"
    with pytest.raises(Problem):
        changes.start_vouch(new, member)  # members do not vouch

    ch = changes.start_vouch(new, secretary)
    new = changes.vouch(new, secretary, challenge_id=ch.id, sms_code=_code(sms_outbox))
    assert new.state == "active"
    assert LedgerEntry.objects.filter(chama=c, type="device_registered",
                                      source_id=new.id).exists()


def test_with_one_other_official_the_members_vote(api, sms_outbox):
    c, (chair, treasurer) = active_chama(since=TODAY, members=2)
    _register(api, chair.user, sms_outbox)
    r, _, _ = _register(api, chair.user, sms_outbox)
    new = Device.objects.get(pk=r.json()["id"])
    v = votes.for_subject(f"device:{new.id}:{c.id}")
    assert v is not None and v.eligible == 1
    ch = votes.start_sms_ballot(v, treasurer, "yes")
    votes.cast_sms(v, treasurer, choice="yes", challenge_id=ch.id, sms_code=_code(sms_outbox))
    new.refresh_from_db()
    assert new.state == "active"


def test_the_old_phone_hands_over_and_the_new_key_works_at_once(api, sms_outbox):
    c, (chair, *_) = active_chama(since=TODAY)
    r, old_key, _ = _register(api, chair.user, sms_outbox)
    old_id = r.json()["id"]

    api.force_authenticate(chair.user)
    nonce = api.post("/api/v1/devices/attest-nonce/", {}, format="json").json()["nonce"]
    new_key, chain, _ = _phone_key(nonce.encode())
    new_pub = base64.b64encode(_spki(new_key)).decode()
    h = api.post(f"/api/v1/devices/{old_id}/handover/", {"new_public_key": new_pub},
                 format="json").json()
    body = {"public_key": new_pub, "platform": "android", "label": "New phone",
            "attestation_chain": chain, "attest_nonce": nonce,
            "handover": {"device_id": old_id, "nonce": h["payload"]["nonce"],
                         "signature": _sign(old_key, h["payload"])}}
    r = api.post("/api/v1/devices/", body, format="json")
    r = api.post("/api/v1/devices/", {**body, "challenge_id": r.json()["challenge_id"],
                                      "code": _code(sms_outbox)}, format="json")
    assert r.status_code == 201, r.content
    assert r.json()["state"] == "active" and r.json()["arrival"] == "handover"
    assert Device.objects.get(pk=old_id).state == "revoked"


def test_a_handover_signed_by_another_key_is_refused(api, sms_outbox):
    c, (chair, *_) = active_chama(since=TODAY)
    r, old_key, _ = _register(api, chair.user, sms_outbox)
    old_id = r.json()["id"]
    api.force_authenticate(chair.user)
    nonce = api.post("/api/v1/devices/attest-nonce/", {}, format="json").json()["nonce"]
    new_key, chain, _ = _phone_key(nonce.encode())
    new_pub = base64.b64encode(_spki(new_key)).decode()
    h = api.post(f"/api/v1/devices/{old_id}/handover/", {"new_public_key": new_pub},
                 format="json").json()
    body = {"public_key": new_pub, "platform": "android", "attestation_chain": chain,
            "attest_nonce": nonce,
            "handover": {"device_id": old_id, "nonce": h["payload"]["nonce"],
                         "signature": _sign(new_key, h["payload"])}}  # not the old phone
    r = api.post("/api/v1/devices/", body, format="json")
    r = api.post("/api/v1/devices/", {**body, "challenge_id": r.json()["challenge_id"],
                                      "code": _code(sms_outbox)}, format="json")
    assert r.status_code == 403 and r.json()["code"] == "signature_invalid"
