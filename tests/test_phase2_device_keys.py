"""Phone signing keys (6.7): registration with attestation, and approvals signed with
the phone's key instead of an SMS code."""

import base64
import datetime as dt
import hashlib
import re
from datetime import timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from django.utils import timezone

from apps.chamas.models import Membership
from apps.core.canonical import canonical_bytes
from apps.core.problems import Problem
from apps.devices import attestation, keys
from apps.devices.models import Device, DeviceNonce
from apps.feeds import services as feeds
from apps.ledger.models import LedgerEntry
from apps.ledger.services import verify_chain
from apps.payouts import services

from .phase1 import active_chama, make_user, statement_csv

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


def _spki(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.DER,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)


def _der_len(n: int) -> bytes:
    return bytes([n]) if n < 0x80 else bytes([0x81, n])


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _der_len(len(value)) + value


def _key_description(level: int, challenge: bytes) -> bytes:
    """attestationVersion, attestationSecurityLevel, keymasterVersion,
    keymasterSecurityLevel, attestationChallenge, uniqueId, softwareEnforced,
    teeEnforced (the parts we read come first)."""
    body = (_tlv(0x02, b"\x03") + _tlv(0x0A, bytes([level])) + _tlv(0x02, b"\x04")
            + _tlv(0x0A, bytes([level])) + _tlv(0x04, challenge) + _tlv(0x04, b"")
            + _tlv(0x30, b"") + _tlv(0x30, b""))
    return _tlv(0x30, body)


def _cert(subject_key, issuer_key, subject, issuer, ext=None):
    now = dt.datetime.now(dt.UTC)
    b = (x509.CertificateBuilder()
         .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
         .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer)]))
         .public_key(subject_key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30)))
    if ext is not None:
        b = b.add_extension(x509.UnrecognizedExtension(attestation.KEY_DESCRIPTION_OID, ext),
                            critical=False)
    return b.sign(issuer_key, hashes.SHA256())


def _phone_key(challenge: bytes, level: int = 1, root=None):
    """A key made 'in the phone' with its attestation chain, leaf first."""
    key = ec.generate_private_key(ec.SECP256R1())
    root = root or ec.generate_private_key(ec.SECP256R1())
    leaf = _cert(key, root, "Android Keystore Key", "Test Attestation Root",
                 _key_description(level, challenge))
    root_cert = _cert(root, root, "Test Attestation Root", "Test Attestation Root")
    chain = [base64.b64encode(c.public_bytes(serialization.Encoding.DER)).decode()
             for c in (leaf, root_cert)]
    return key, chain, root


def _register(api, user, outbox, *, level=1, tamper=None, root=None):
    api.force_authenticate(user)
    nonce = api.post("/api/v1/devices/attest-nonce/", {}, format="json").json()["nonce"]
    key, chain, root = _phone_key(nonce.encode(), level, root)
    body = {"public_key": base64.b64encode(_spki(key)).decode(), "platform": "android",
            "label": "Pixel 7", "attestation_chain": chain, "attest_nonce": nonce}
    if tamper:
        body = tamper(body)
    r = api.post("/api/v1/devices/", body, format="json")
    assert r.status_code == 202, r.content
    r = api.post("/api/v1/devices/", {**body, "challenge_id": r.json()["challenge_id"],
                                      "code": _code(outbox)}, format="json")
    return r, key, root


def test_an_official_registers_their_phone_key_with_an_sms_code(api, sms_outbox):
    chama, (chair, *_) = active_chama(since=TODAY)
    r, key, _ = _register(api, chair.user, sms_outbox, level=2)
    assert r.status_code == 201, r.content
    d = r.json()
    assert d["security_level"] == "strongbox" and d["state"] == "active"
    assert d["fingerprint"] == hashlib.sha256(_spki(key)).hexdigest()
    assert d["attestation_root_trusted"] is False  # no trusted roots configured
    entry = LedgerEntry.objects.get(chama=chama, type="device_registered")
    assert entry.member_id == chair.id and entry.decision["security_level"] == "strongbox"
    assert api.get("/api/v1/devices/").json()[0]["label"] == "Pixel 7"


@pytest.mark.parametrize("tamper,why", [
    (lambda b: {**b, "attestation_chain": list(reversed(b["attestation_chain"]))},
     "different key"),
    (lambda b: {**b, "attestation_chain": b["attestation_chain"][:1] + [
        _phone_key(b"x")[1][1]]}, "chain is broken"),
])
def test_a_bad_attestation_is_refused(api, sms_outbox, tamper, why):
    _, (chair, *_) = active_chama(since=TODAY)
    r, _, _ = _register(api, chair.user, sms_outbox, tamper=tamper)
    assert r.status_code == 400 and r.json()["code"] == "attestation_invalid"
    assert why in r.json()["detail"]


def test_a_key_made_for_another_challenge_is_refused(api, sms_outbox):
    _, (chair, *_) = active_chama(since=TODAY)
    api.force_authenticate(chair.user)
    nonce = api.post("/api/v1/devices/attest-nonce/", {}, format="json").json()["nonce"]
    key, chain, _ = _phone_key(b"someone-elses-challenge")
    body = {"public_key": base64.b64encode(_spki(key)).decode(), "platform": "android",
            "attestation_chain": chain, "attest_nonce": nonce}
    ch = api.post("/api/v1/devices/", body, format="json").json()["challenge_id"]
    r = api.post("/api/v1/devices/", {**body, "challenge_id": ch, "code": _code(sms_outbox)},
                 format="json")
    assert r.json()["code"] == "attestation_invalid"


def test_production_can_require_a_trusted_root(api, sms_outbox, settings):
    _, (chair, treasurer, _) = active_chama(since=TODAY)
    settings.DEVICE_REQUIRE_TRUSTED_ATTESTATION = True
    r, _, _ = _register(api, chair.user, sms_outbox)
    assert r.status_code == 403 and r.json()["code"] == "attestation_untrusted"

    google = ec.generate_private_key(ec.SECP256R1())
    settings.ANDROID_ATTESTATION_ROOTS = [hashlib.sha256(_spki(google)).hexdigest()]
    r, _, _ = _register(api, treasurer.user, sms_outbox, root=google)
    assert r.status_code == 201 and r.json()["attestation_root_trusted"] is True


# --- Approving with the phone's key ---------------------------------------------------------

@pytest.fixture
def money(settings, sms_outbox):
    settings.BANK_STATEMENT_SENDERS = {"equity": ["equitybank.example"]}
    c, (chair, treasurer, secretary) = active_chama(since=TODAY)
    member = Membership.objects.create(chama=c, user=make_user("+254799000666", "Mary"),
                                       joined_on=TODAY, member_code=f"{c.member_code_prefix}-004")
    data = statement_csv([(TODAY - timedelta(days=2), "DEPOSIT", 0, 10_000_000)],
                         opening_cents=0)
    feeds.receive_mail({
        "to": c.statement_inbox.address, "from": "s@equitybank.example",
        "auth": {"spf": "pass", "dkim": "pass", "dmarc": "pass",
                 "dkim_domain": "equitybank.example"},
        "attachments": [{"filename": "s.csv", "content_b64": base64.b64encode(data).decode()}]})
    for o in (chair, treasurer):
        ch = services.start_enable(c, o)
        services.enable(c, o, challenge_id=ch.id, sms_code=_code(sms_outbox))
    c.refresh_from_db()
    p = services.propose(c, chair, kind="payout", amount_cents=2_000_000, payee=member,
                         purpose="Turn")
    return c, chair, treasurer, member, p


def _device_for(user):
    key = ec.generate_private_key(ec.SECP256R1())
    device = Device.objects.create(user=user, public_key_der=_spki(key),
                                   fingerprint=hashlib.sha256(_spki(key)).hexdigest(),
                                   platform="android", security_level="tee")
    return key, device


def _sign(key, payload) -> str:
    return base64.b64encode(key.sign(canonical_bytes(payload), ec.ECDSA(hashes.SHA256()))
                            ).decode()


def test_an_official_with_a_phone_key_approves_with_it(api, money, sms_outbox):
    c, chair, treasurer, _, p = money
    key, device = _device_for(chair.user)
    api.force_authenticate(chair.user)

    # The SMS code is refused for an official whose phone holds a key.
    r = api.post(f"/api/v1/proposals/{p.id}/approve/", {}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "use_device_key"

    r = api.post(f"/api/v1/proposals/{p.id}/signing-payload/", {}, format="json")
    assert r.status_code == 200, r.content
    body = r.json()
    payload = body["payload"]
    assert payload["proposal_id"] == str(p.id) and payload["amount_cents"] == 2_000_000
    assert body["payload_hash"] == hashlib.sha256(canonical_bytes(payload)).hexdigest()
    assert body["device_id"] == str(device.id)

    r = api.post(f"/api/v1/proposals/{p.id}/signatures/", {
        "device_id": str(device.id), "nonce": payload["nonce"],
        "payload_hash": body["payload_hash"], "signature": _sign(key, payload)}, format="json")
    assert r.status_code == 200, r.content
    assert r.json()["approvals"][0]["method"] == "device"

    # The same nonce cannot be used again.
    r = api.post(f"/api/v1/proposals/{p.id}/signatures/", {
        "device_id": str(device.id), "nonce": payload["nonce"],
        "payload_hash": body["payload_hash"], "signature": _sign(key, payload)}, format="json")
    assert r.status_code == 409


def test_a_signature_over_anything_else_is_refused(api, money):
    c, chair, *_, p = money
    key, device = _device_for(chair.user)
    api.force_authenticate(chair.user)
    body = api.post(f"/api/v1/proposals/{p.id}/signing-payload/", {}, format="json").json()
    payload = body["payload"]

    shown_more = {**payload, "amount_cents": 99}  # the phone signed a different amount
    r = api.post(f"/api/v1/proposals/{p.id}/signatures/", {
        "device_id": str(device.id), "nonce": payload["nonce"],
        "payload_hash": body["payload_hash"], "signature": _sign(key, shown_more)},
        format="json")
    assert r.status_code == 400 and r.json()["code"] == "signature_invalid"

    body = api.post(f"/api/v1/proposals/{p.id}/signing-payload/", {}, format="json").json()
    r = api.post(f"/api/v1/proposals/{p.id}/signatures/", {
        "device_id": str(device.id), "nonce": body["payload"]["nonce"],
        "payload_hash": "0" * 64, "signature": _sign(key, body["payload"])}, format="json")
    assert r.status_code == 409 and r.json()["code"] == "payload_changed"


def test_someone_elses_key_cannot_approve(api, money):
    c, chair, treasurer, *_, p = money
    _, device = _device_for(treasurer.user)
    key, _ = _device_for(chair.user)
    api.force_authenticate(chair.user)
    body = api.post(f"/api/v1/proposals/{p.id}/signing-payload/", {}, format="json").json()
    r = api.post(f"/api/v1/proposals/{p.id}/signatures/", {
        "device_id": str(device.id), "nonce": body["payload"]["nonce"],
        "payload_hash": body["payload_hash"], "signature": _sign(key, body["payload"])},
        format="json")
    assert r.status_code == 403 and r.json()["code"] == "device_invalid"


def test_phone_signatures_are_bound_into_the_payout_entry(api, money, sms_outbox):
    c, chair, treasurer, _, p = money
    key, device = _device_for(chair.user)
    body = services.start_device_approve(p, chair)
    services.approve_with_device(p, chair, device_id=device.id,
                                 nonce=body["payload"]["nonce"],
                                 payload_hash=body["payload_hash"],
                                 signature_b64=_sign(key, body["payload"]))
    ch = services.start_approve(p, treasurer)  # no phone key: SMS
    services.approve(p, treasurer, challenge_id=ch.id, sms_code=_code(sms_outbox))
    p.refresh_from_db()
    assert p.state == "approved"
    services.mark_paid(p, treasurer, "")
    data = statement_csv([(TODAY, "TRANSFER MARY", 2_000_000, 0)], opening_cents=10_000_000)
    feeds.receive_mail({
        "to": c.statement_inbox.address, "from": "s@equitybank.example",
        "auth": {"spf": "pass", "dkim": "pass", "dmarc": "pass",
                 "dkim_domain": "equitybank.example"},
        "attachments": [{"filename": "t.csv", "content_b64": base64.b64encode(data).decode()}]})
    p.refresh_from_db()
    entry = p.entry
    assert entry.signatures_digest is not None
    assert [a["method"] for a in entry.decision["approved_by"]] == ["device", "sms"]
    assert verify_chain(c, check_signatures=False).ok


def test_a_revoked_key_falls_back_to_sms(api, money, sms_outbox):
    c, chair, *_, p = money
    _, device = _device_for(chair.user)
    with pytest.raises(Problem):
        services.start_approve(p, chair)
    keys.revoke(device, chair.user)
    assert services.start_approve(p, chair)  # SMS again
    assert DeviceNonce.objects.count() == 0
