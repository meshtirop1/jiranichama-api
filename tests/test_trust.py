"""The cryptographic protections of 2026-10-20: the endorsed platform key chain, member
countersignatures, the public OpenTimestamps proof, and the public lot for elections
tied twice."""

import base64
import hashlib
import re
from datetime import timedelta
from unittest import mock

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.utils import timezone

from apps.chamas.models import Membership
from apps.core.canonical import canonical_bytes
from apps.devices.models import Device
from apps.elections import services as elections
from apps.ledger import anchoring, signing, trust
from apps.ledger.models import ChainHead, LedgerEntry, PlatformKey
from apps.votes import services as votes

from .phase1 import active_chama
from .test_phase2_device_keys import _spki

pytestmark = pytest.mark.django_db
TODAY = timezone.localdate()


def _pem(key) -> str:
    return key.public_key().public_bytes(serialization.Encoding.PEM,
                                         serialization.PublicFormat.SubjectPublicKeyInfo
                                         ).decode()


def _new_key_pem(settings):
    new = ec.generate_private_key(ec.SECP256R1())
    return new, new.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()).decode()


def test_the_first_key_starts_the_chain_and_a_new_key_needs_the_old_ones_blessing(
        api, settings):
    first = trust.ensure_current_key()
    assert first.prev_key_id == "" and PlatformKey.objects.count() == 1

    # A different key, not endorsed, must never sign: phones would reject it anyway.
    other, other_pem = _new_key_pem(settings)
    settings.PLATFORM_SIGNING_KEY_PEM = other_pem
    signing.get_signer.cache_clear()
    with pytest.raises(RuntimeError):
        trust.ensure_current_key()
    assert PlatformKey.objects.count() == 1


def test_rotation_endorsed_by_the_old_key_verifies(api, settings, platform_key):
    trust.ensure_current_key()
    new, new_private = _new_key_pem(settings)
    new_id = "local:" + hashlib.sha256(new.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    ).hexdigest()[:16]
    k = trust.endorse(new_key_id=new_id, new_public_key_pem=_pem(new))
    assert k.prev_key_id and k.endorsement_sig
    keys = list(PlatformKey.objects.order_by("created_at"))
    assert trust.verify_chain(keys)

    # Switch to the new key: it signs, because it is on the chain.
    settings.PLATFORM_SIGNING_KEY_PEM = new_private
    signing.get_signer.cache_clear()
    assert trust.ensure_current_key().key_id == new_id

    chain = api.get("/api/v1/platform-keys/").json()
    assert [c["key_id"] for c in chain][-1] == new_id
    assert chain[1]["prev_key_id"] == chain[0]["key_id"] and chain[1]["endorsement_sig"]

    # A forged endorsement (signed by someone else) does not verify.
    forger = ec.generate_private_key(ec.SECP256R1())
    payload = trust.endorsement_payload(key_id="evil", fingerprint="0" * 64,
                                        prev_key_id=new_id)
    bad = PlatformKey(key_id="evil", fingerprint="0" * 64, public_key_pem=_pem(forger),
                      prev_key_id=new_id, endorsement_sig=forger.sign(
                          hashlib.sha256(canonical_bytes(payload)).digest(),
                          ec.ECDSA(hashes.SHA256())))
    assert not trust.verify_chain(keys + [bad])


def _record(c, m):
    from apps.ledger import services as ledger

    return ledger.append(chama=c, type="membership", source="test", member=m,
                         decision={"change": "joined"})


def _device(user):
    key = ec.generate_private_key(ec.SECP256R1())
    Device.objects.create(user=user, public_key_der=_spki(key),
                          fingerprint=hashlib.sha256(_spki(key)).hexdigest(),
                          platform="android", security_level="tee")
    return key


def test_members_countersign_the_chain_they_checked(api):
    c, (chair, treasurer, secretary, mary, john) = active_chama(since=TODAY, members=5)
    _record(c, mary)
    head = ChainHead.objects.get(chama=c)
    key = _device(mary.user)
    payload = trust.countersign_payload(chama_id=c.id, member_code=mary.member_code,
                                        seq=head.seq, head_hash=head.hash)
    sig = base64.b64encode(key.sign(canonical_bytes(payload), ec.ECDSA(hashes.SHA256()))
                           ).decode()
    api.force_authenticate(mary.user)
    url = f"/api/v1/chamas/{c.id}/countersignatures/"
    r = api.post(url, {"seq": head.seq, "hash": head.hash, "signature": sig}, format="json")
    assert r.status_code == 201
    [mine] = r.json()
    assert mine["member_code"] == mary.member_code and mine["hash"] == head.hash

    # Every member can check it with the device key that is included.
    pub = serialization.load_der_public_key(base64.b64decode(mine["device_public_key"]))
    pub.verify(base64.b64decode(mine["signature"]), canonical_bytes(payload),
               ec.ECDSA(hashes.SHA256()))

    # A different history than the server's is refused loudly.
    r = api.post(url, {"seq": head.seq, "hash": "f" * 64, "signature": sig}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "head_mismatch"
    # A signature by another key is refused.
    forged = base64.b64encode(ec.generate_private_key(ec.SECP256R1()).sign(
        canonical_bytes(payload), ec.ECDSA(hashes.SHA256()))).decode()
    r = api.post(url, {"seq": head.seq, "hash": head.hash, "signature": forged},
                 format="json")
    assert r.status_code == 400

    # Without a phone key, nothing to sign with.
    api.force_authenticate(john.user)
    r = api.post(url, {"seq": head.seq, "hash": head.hash, "signature": sig}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "no_device_key"


def test_the_ots_proof_downloads_for_anyone(api, settings):
    c, ms = active_chama(since=TODAY, members=5)
    _record(c, ms[0])
    settings.OTS_CALENDARS = ["https://calendar.example"]
    settings.ANCHOR_EMAIL_TO = []
    resp = mock.Mock(status_code=200, content=b"\x00OpenTimestamps proof")
    with mock.patch("apps.ledger.anchoring.requests.post", return_value=resp):
        a = anchoring.anchor_now()
    r = api.get(f"/api/v1/anchors/{a.id}/ots/")
    assert r.status_code == 200 and r.content == b"\x00OpenTimestamps proof"
    assert r["X-Merkle-Root"] == a.merkle_root


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


def _split(v, outbox):
    """Half the eligible members say yes, half say no."""
    people = list(Membership.objects.filter(pk__in=v.eligible_ids).order_by("member_code"))
    half = len(people) // 2
    for i, m in enumerate(people):
        choice = "yes" if i < half else "no"
        ch = votes.start_sms_ballot(v, m, choice)
        v = votes.cast_sms(v, m, choice=choice, challenge_id=ch.id, sms_code=_code(outbox))
    return v


def test_a_tied_election_is_held_again_then_settled_by_the_public_lot(sms_outbox, settings):
    settings.OTS_CALENDARS = []
    settings.ANCHOR_EMAIL_TO = []
    c, ms = active_chama(since=TODAY, members=6)
    chair, treasurer, secretary, mary, *_ = ms
    e = elections.nominate(c, chair, role="secretary", candidate_id=mary.id)
    v = votes.for_subject(f"elections.election:{e.id}")
    assert len(v.eligible_ids) % 2 == 0
    v = _split(v, sms_outbox)
    v.refresh_from_db()
    if v.state == "open":
        votes._close(v)
    e.refresh_from_db()
    assert e.state == "voting" and e.tie_round == 1  # held again
    v2 = votes.for_subject(f"elections.election:{e.id}")
    assert v2.id != v.id and v2.question.startswith("Tied. Vote again")

    v2 = _split(v2, sms_outbox)
    v2.refresh_from_db()
    if v2.state == "open":
        votes._close(v2)
    e.refresh_from_db()
    assert e.state == "awaiting_lot"

    with mock.patch("django.utils.timezone.now",
                    return_value=timezone.now() + timedelta(hours=12)):
        a = anchoring.anchor_now()
    e.refresh_from_db()
    draw = elections.lot_draw(a.merkle_root, e.id)
    expected = "won" if int(draw[-1], 16) % 2 == 0 else "lost"
    assert e.state in (expected, "in_office")
    lot = LedgerEntry.objects.get(chama=c, type="vote_result", decision__lot=True)
    assert lot.decision["draw"] == draw and lot.decision["merkle_root"] == a.merkle_root


def test_nothing_signs_with_a_key_off_the_chain(settings):
    from apps.ledger import services as ledger
    from apps.ledger.models import EntrySignature

    c, ms = active_chama(since=TODAY, members=5)
    _record(c, ms[0])
    trust.ensure_current_key()
    _, other_pem = _new_key_pem(settings)
    settings.PLATFORM_SIGNING_KEY_PEM = other_pem
    signing.get_signer.cache_clear()
    unsigned = LedgerEntry.objects.filter(chama=c).exclude(
        pk__in=EntrySignature.objects.values("entry")).first()
    assert unsigned is not None
    with pytest.raises(RuntimeError):
        ledger.sign_entry(unsigned.pk)
    assert not EntrySignature.objects.filter(entry=unsigned).exists()


def test_the_shared_vectors_verify_here_as_they_do_on_the_phone():
    import json
    from pathlib import Path

    v = json.loads((Path(__file__).resolve().parent.parent / "vectors" / "key_chain.json")
                   .read_text(encoding="utf-8"))

    def key(d):
        return PlatformKey(key_id=d["key_id"], fingerprint=d["fingerprint"],
                           public_key_pem=d["public_key_pem"],
                           prev_key_id=d["prev_key_id"] or "",
                           endorsement_sig=base64.b64decode(d["endorsement_sig"])
                           if d["endorsement_sig"] else None)

    chain = [key(d) for d in v["chain"]]
    assert chain[0].fingerprint == v["pinned_fingerprint"]
    assert trust.verify_chain(chain)
    assert not trust.verify_chain(chain + [key(v["forged_next"])])
    cs = v["countersignature"]
    pub = serialization.load_der_public_key(base64.b64decode(cs["device_public_key"]))
    pub.verify(base64.b64decode(cs["signature"]), canonical_bytes(cs["payload"]),
               ec.ECDSA(hashes.SHA256()))
