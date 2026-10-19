"""Members' votes on payouts above the vote level (rules 4.x and 9.1, spec 5.11)."""

import base64
import hashlib
import re
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.utils import timezone

from apps.chamas.models import Membership
from apps.core.canonical import canonical_bytes
from apps.core.problems import Problem
from apps.devices.models import Device
from apps.feeds import services as feeds
from apps.ledger.models import LedgerEntry
from apps.ledger.services import verify_chain
from apps.payouts import services as payouts
from apps.rules.templates import template_for
from apps.votes import services as votes
from apps.votes.models import Ballot

from .phase1 import active_chama, make_user, statement_csv

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


def _setup(settings, outbox, *, secret=False):
    """Three officials and two members, money out on, KES 100,000 reconciled; a payout of
    KES 60,000 to Mary: above KES 50,000, so 2 officials and a members' vote."""
    settings.BANK_STATEMENT_SENDERS = {"equity": ["equitybank.example"]}
    rules = template_for("merry_go_round")
    rules["votes"] = {"quorum_pct": 50, "pass_pct": 50, "secret": secret}
    c, (chair, treasurer, secretary) = active_chama(since=TODAY, rules=rules)
    mary = Membership.objects.create(chama=c, user=make_user("+254799000801", "Mary"),
                                     joined_on=TODAY, member_code=f"{c.member_code_prefix}-004")
    john = Membership.objects.create(chama=c, user=make_user("+254799000802", "John"),
                                     joined_on=TODAY, member_code=f"{c.member_code_prefix}-005")
    data = statement_csv([(TODAY - timedelta(days=1), "DEPOSIT", 0, 10_000_000)],
                         opening_cents=0)
    feeds.receive_mail({
        "to": c.statement_inbox.address, "from": "s@equitybank.example",
        "auth": {"spf": "pass", "dkim": "pass", "dmarc": "pass",
                 "dkim_domain": "equitybank.example"},
        "attachments": [{"filename": "s.csv", "content_b64": base64.b64encode(data).decode()}]})
    for o in (chair, treasurer):
        ch = payouts.start_enable(c, o)
        payouts.enable(c, o, challenge_id=ch.id, sms_code=_code(outbox))
    c.refresh_from_db()
    p = payouts.propose(c, chair, kind="payout", amount_cents=6_000_000, payee=mary,
                        purpose="Welfare: hospital bill")
    return c, chair, treasurer, secretary, mary, john, p


def _vote(member, v, choice, outbox):
    ch = votes.start_sms_ballot(v, member, choice)
    return votes.cast_sms(v, member, choice=choice, challenge_id=ch.id,
                          sms_code=_code(outbox))


def _approve(p, official, outbox):
    ch = payouts.start_approve(p, official)
    payouts.approve(p, official, challenge_id=ch.id, sms_code=_code(outbox))


def test_a_large_payout_opens_a_vote_and_needs_it_to_pass(settings, sms_outbox):
    c, chair, treasurer, secretary, mary, john, p = _setup(settings, sms_outbox)
    assert p.state == "collecting_approvals" and p.needs_vote
    v = votes.for_subject(f"payouts.proposal:{p.id}")
    assert v.state == "open" and v.eligible == 4  # Mary is paid, so she does not vote
    assert str(mary.id) not in v.eligible_ids
    assert v.question == "Pay Mary KES 60,000 for Welfare: hospital bill?"
    assert v.closes_at == p.deadline_at

    _approve(p, chair, sms_outbox)
    _approve(p, treasurer, sms_outbox)
    p.refresh_from_db()
    assert p.state == "collecting_approvals"  # officials done; the members have not

    _vote(chair, v, "yes", sms_outbox)
    _vote(treasurer, v, "yes", sms_outbox)
    _vote(secretary, v, "no", sms_outbox)
    v = _vote(john, v, "yes", sms_outbox)  # everyone eligible has voted: it closes
    assert (v.state, v.yes, v.no) == ("passed", 3, 1)
    p.refresh_from_db()
    assert p.state == "approved"
    entry = LedgerEntry.objects.get(pk=v.result_entry_id)
    assert entry.type == "vote_result" and entry.decision["result"] == "passed"
    assert len(entry.decision["voted"]) == 4
    assert verify_chain(c, check_signatures=False).ok


def test_an_even_split_is_not_a_pass_and_the_payout_is_rejected(settings, sms_outbox):
    c, chair, treasurer, secretary, mary, john, p = _setup(settings, sms_outbox)
    v = votes.for_subject(f"payouts.proposal:{p.id}")
    _vote(chair, v, "yes", sms_outbox)
    _vote(treasurer, v, "yes", sms_outbox)
    _vote(secretary, v, "no", sms_outbox)
    v = _vote(john, v, "no", sms_outbox)
    assert v.state == "failed"
    p.refresh_from_db()
    assert p.state == "rejected" and p.state_reason == "The members voted no."


def test_too_few_voters_by_the_deadline_lapses_it(settings, sms_outbox, monkeypatch):
    c, chair, *_, p = _setup(settings, sms_outbox)
    v = votes.for_subject(f"payouts.proposal:{p.id}")
    _vote(chair, v, "yes", sms_outbox)  # 1 of 4; quorum is 2
    later = v.closes_at + timedelta(minutes=1)
    monkeypatch.setattr(votes.timezone, "now", lambda: later)
    monkeypatch.setattr(payouts.timezone, "now", lambda: later)
    payouts.expire_due(c)
    v.refresh_from_db()
    p.refresh_from_db()
    assert v.state == "lapsed"
    assert p.state == "rejected" and p.state_reason == "Not enough members voted."


def test_each_member_votes_once_and_the_payee_not_at_all(settings, sms_outbox):
    c, chair, treasurer, secretary, mary, john, p = _setup(settings, sms_outbox)
    v = votes.for_subject(f"payouts.proposal:{p.id}")
    _vote(john, v, "yes", sms_outbox)
    with pytest.raises(Problem) as exc:
        votes.start_sms_ballot(v, john, "no")
    assert exc.value.problem_code == "already_voted"
    with pytest.raises(Problem) as exc:
        votes.start_sms_ballot(v, mary, "yes")
    assert exc.value.problem_code == "not_eligible"


def test_a_secret_vote_does_not_store_choices_with_names(settings, sms_outbox, api):
    c, chair, treasurer, secretary, mary, john, p = _setup(settings, sms_outbox, secret=True)
    v = votes.for_subject(f"payouts.proposal:{p.id}")
    _vote(chair, v, "yes", sms_outbox)
    _vote(john, v, "no", sms_outbox)
    assert all(b.membership_id is None for b in Ballot.objects.filter(vote=v))
    api.force_authenticate(john.user)
    body = api.get(f"/api/v1/votes/{v.id}/").json()
    assert body["my_choice"] is None and body["i_voted"] is True
    assert body["voted"] == ["Founder", "John"]  # who voted is public
    assert body["yes"] is None  # no tally until it closes


def test_a_member_with_a_phone_key_votes_with_it(settings, sms_outbox, api):
    c, chair, treasurer, secretary, mary, john, p = _setup(settings, sms_outbox)
    v = votes.for_subject(f"payouts.proposal:{p.id}")
    key = ec.generate_private_key(ec.SECP256R1())
    spki = key.public_key().public_bytes(serialization.Encoding.DER,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)
    device = Device.objects.create(user=john.user, public_key_der=spki,
                                   fingerprint=hashlib.sha256(spki).hexdigest(),
                                   platform="android")
    with pytest.raises(Problem) as exc:
        votes.start_sms_ballot(v, john, "yes")
    assert exc.value.problem_code == "use_device_key"

    api.force_authenticate(john.user)
    body = api.post(f"/api/v1/votes/{v.id}/signing-payload/", {"choice": "yes"},
                    format="json").json()
    payload = body["payload"]
    assert payload["choice"] == "yes" and payload["vote_id"] == str(v.id)
    sig = base64.b64encode(key.sign(canonical_bytes(payload), ec.ECDSA(hashes.SHA256()))
                           ).decode()
    r = api.post(f"/api/v1/votes/{v.id}/signed-ballots/", {
        "choice": "yes", "device_id": str(device.id), "nonce": payload["nonce"],
        "payload_hash": body["payload_hash"], "signature": sig}, format="json")
    assert r.status_code == 201, r.content
    assert r.json()["i_voted"] is True and r.json()["my_choice"] == "yes"
    b = Ballot.objects.get(vote=v)
    assert b.method == "device" and b.device_id == device.id

    # A signature over the other choice does not count as this one.
    key2 = ec.generate_private_key(ec.SECP256R1())
    spki2 = key2.public_key().public_bytes(serialization.Encoding.DER,
                                           serialization.PublicFormat.SubjectPublicKeyInfo)
    d2 = Device.objects.create(user=secretary.user, public_key_der=spki2,
                               fingerprint=hashlib.sha256(spki2).hexdigest(), platform="android")
    api.force_authenticate(secretary.user)
    body = api.post(f"/api/v1/votes/{v.id}/signing-payload/", {"choice": "no"},
                    format="json").json()
    signed_yes = {**body["payload"], "choice": "yes"}
    sig = base64.b64encode(key2.sign(canonical_bytes(signed_yes), ec.ECDSA(hashes.SHA256()))
                           ).decode()
    r = api.post(f"/api/v1/votes/{v.id}/signed-ballots/", {
        "choice": "no", "device_id": str(d2.id), "nonce": body["payload"]["nonce"],
        "payload_hash": body["payload_hash"], "signature": sig}, format="json")
    assert r.status_code == 400 and r.json()["code"] == "signature_invalid"


def test_votes_through_the_api(settings, sms_outbox, api):
    c, chair, treasurer, secretary, mary, john, p = _setup(settings, sms_outbox)
    api.force_authenticate(john.user)
    [v] = api.get(f"/api/v1/chamas/{c.id}/votes/").json()
    assert v["i_can_vote"] is True and v["quorum_needed"] == 2 and v["eligible"] == 4
    r = api.post(f"/api/v1/votes/{v['id']}/ballots/", {"choice": "yes"}, format="json")
    assert r.status_code == 202
    r = api.post(f"/api/v1/votes/{v['id']}/ballots/", {
        "choice": "yes", "challenge_id": r.json()["challenge_id"],
        "code": _code(sms_outbox)}, format="json")
    assert r.status_code == 201 and r.json()["cast"] == 1 and r.json()["my_choice"] == "yes"
    api.force_authenticate(mary.user)
    r = api.get(f"/api/v1/proposals/{p.id}/").json()
    assert r["needs_vote"] is True and r["vote_state"] == "open"
    assert api.get(f"/api/v1/votes/{v['id']}/").json()["i_can_vote"] is False
