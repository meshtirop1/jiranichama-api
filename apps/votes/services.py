"""Members' votes (5.11). See docs/flows/votes.md.

A vote opens with its settings copied from the rules. Each eligible member votes once,
by their phone key (fingerprint) or an SMS code. It closes when everyone has voted or at
its closing time; then quorum and threshold are checked and the result is written to the
chama's ledger.
"""

import base64
import hashlib
import math
import secrets

from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.chamas.models import Chama, Membership
from apps.core.canonical import canonical_bytes
from apps.core.crypto import keyed_hash
from apps.core.problems import Problem
from apps.devices import attestation, keys, signing
from apps.devices.models import Device, DeviceNonce, SigningChallenge
from apps.ledger import services as ledger

from .models import Ballot, Turnout, Vote, VoteSmsNumber

DEFAULTS = {"quorum_pct": 50, "pass_pct": 50, "secret": False}


def settings_from(rules: dict) -> dict:
    return {**DEFAULTS, **(rules.get("votes") or {})}


def open_vote(chama: Chama, *, subject: str, question: str, rules: dict, closes_at,
              exclude: list | None = None, quorum_pct: int | None = None,
              pass_pct: int | None = None) -> Vote:
    s = settings_from(rules)
    if quorum_pct is not None:
        s["quorum_pct"] = quorum_pct
    if pass_pct is not None:
        s["pass_pct"] = pass_pct
    eligible = [str(i) for i in Membership.objects.filter(
        chama=chama, state=Membership.State.ACTIVE).exclude(pk__in=exclude or [])
        .values_list("id", flat=True)]
    vote = Vote.objects.create(chama=chama, subject=subject, question=question[:300],
                               quorum_pct=s["quorum_pct"], pass_pct=s["pass_pct"],
                               secret=s["secret"], eligible=len(eligible),
                               eligible_ids=eligible, closes_at=closes_at)
    transaction.on_commit(lambda: sms_invites(vote.pk))
    return vote


def _free_number(member: Membership) -> str:
    taken = set(VoteSmsNumber.objects.filter(membership__user=member.user,
                                             vote__state=Vote.State.OPEN)
                .values_list("number", flat=True))
    while True:
        n = f"{secrets.randbelow(9000) + 1000}"
        if n not in taken:
            return n


def sms_invites(vote_id) -> int:
    """Every eligible member without a phone key gets the question by SMS, with their own
    number to reply YES or NO with (5.11 step 2; basic phones)."""
    from apps.notify.sms import SmsFailed, send_sms

    vote = Vote.objects.select_related("chama").get(pk=vote_id)
    sent = 0
    for m in (Membership.objects.filter(pk__in=vote.eligible_ids).select_related("user")):
        if keys.active_device(m.user) is not None:
            continue  # votes in the app with the fingerprint
        n = VoteSmsNumber.objects.create(vote=vote, membership=m, number=_free_number(m))
        by = timezone.localtime(vote.closes_at).strftime("%d %b %H:%M")
        if m.user.language == "sw":
            text = (f"{vote.chama.name} kura: {vote.question} Jibu NDIYO {n.number} au "
                    f"HAPANA {n.number} kabla ya {by}.")
        else:
            text = (f"{vote.chama.name} vote: {vote.question} Reply YES {n.number} or "
                    f"NO {n.number} by {by}.")
        try:
            send_sms(m.user.phone, text)
            sent += 1
        except SmsFailed:
            continue
    return sent


def cast_sms_reply(user, *, choice: str, number: str) -> Vote:
    """A reply "YES 4821" from the member's own number (the provider tells us the
    sender). The number names the vote; the first ballot counts."""
    n = (VoteSmsNumber.objects.filter(membership__user=user, number=number)
         .select_related("vote", "membership").order_by("-id").first())
    if n is None:
        raise Problem("not_found", "That vote number is not known.", status.HTTP_404_NOT_FOUND)
    if keys.active_device(user) is not None:
        raise Problem("use_device_key", "Vote in the app with your fingerprint.",
                      status.HTTP_409_CONFLICT)
    _require_can_vote(n.vote, n.membership)
    return _record(n.vote, n.membership, _choice(choice), method="reply")


def quorum_needed(vote: Vote) -> int:
    return max(1, math.ceil(vote.eligible * vote.quorum_pct / 100))


def _voter_hash(vote: Vote, member: Membership) -> str:
    return keyed_hash("ballot", f"{vote.id}:{member.id}")


def _require_can_vote(vote: Vote, by: Membership) -> None:
    close_due(vote.chama)
    vote.refresh_from_db()
    if vote.state != Vote.State.OPEN:
        raise Problem("vote_closed", "This vote has closed.", status.HTTP_409_CONFLICT)
    if str(by.id) not in vote.eligible_ids:
        raise Problem("not_eligible", "You do not vote on this one.", status.HTTP_403_FORBIDDEN)
    if Turnout.objects.filter(vote=vote, membership=by).exists():
        raise Problem("already_voted", "You have already voted. The first vote counts.",
                      status.HTTP_409_CONFLICT)


def _choice(raw: str) -> str:
    if raw not in Ballot.Choice.values:
        raise Problem("choice_invalid", "Vote yes or no.")
    return raw


def _content_hash(vote: Vote, choice: str) -> str:
    return hashlib.sha256(canonical_bytes({"vote": str(vote.id), "choice": choice})).hexdigest()


# --- By SMS code ---------------------------------------------------------------------------

def start_sms_ballot(vote: Vote, by: Membership, choice: str) -> SigningChallenge:
    _require_can_vote(vote, by)
    choice = _choice(choice)
    if keys.active_device(by.user) is not None:
        raise Problem("use_device_key", "This phone is set up for signing: vote with your "
                                        "fingerprint in the app.", status.HTTP_409_CONFLICT)
    word = {"en": {"yes": "YES", "no": "NO"}, "sw": {"yes": "NDIYO", "no": "HAPANA"}}
    lang = "sw" if by.user.language == "sw" else "en"
    action = (f"VOTE {word['en'][choice]}: {vote.question}" if lang == "en"
              else f"KUPIGA KURA {word['sw'][choice]}: {vote.question}")
    return signing.issue(user=by.user, purpose=SigningChallenge.Purpose.CAST_BALLOT,
                         subject=f"votes.vote:{vote.id}", content_hash=_content_hash(vote, choice),
                         action_text=action)


def cast_sms(vote: Vote, by: Membership, *, choice: str, challenge_id, sms_code: str) -> Vote:
    _require_can_vote(vote, by)
    choice = _choice(choice)
    signing.confirm(user=by.user, challenge_id=challenge_id, code=sms_code,
                    purpose=SigningChallenge.Purpose.CAST_BALLOT,
                    subject=f"votes.vote:{vote.id}", content_hash=_content_hash(vote, choice))
    return _record(vote, by, choice, method="sms")


# --- By phone key (6.7) --------------------------------------------------------------------

def signing_payload(vote: Vote, nonce: str, choice: str) -> dict:
    return {"v": 1, "purpose": "cast_ballot", "chama_id": str(vote.chama_id),
            "vote_id": str(vote.id), "question": vote.question, "choice": choice,
            "nonce": nonce}


def start_device_ballot(vote: Vote, by: Membership, choice: str) -> dict:
    _require_can_vote(vote, by)
    choice = _choice(choice)
    device = keys.active_device(by.user)
    if device is None:
        raise Problem("no_device_key", "Set up this phone for signing first.",
                      status.HTTP_409_CONFLICT)
    n = keys.issue_nonce(by.user, DeviceNonce.Use.SIGN, f"votes.vote:{vote.id}")
    payload = signing_payload(vote, n.nonce, choice)
    return {"device_id": str(device.id), "payload": payload,
            "payload_hash": hashlib.sha256(canonical_bytes(payload)).hexdigest()}


def cast_device(vote: Vote, by: Membership, *, choice: str, device_id, nonce: str,
                payload_hash: str, signature_b64: str) -> Vote:
    choice = _choice(choice)
    device = Device.objects.filter(pk=device_id, user=by.user,
                                   state=Device.State.ACTIVE).first()
    if device is None:
        raise Problem("device_invalid", "This phone's key is not active.",
                      status.HTTP_403_FORBIDDEN)
    _require_can_vote(vote, by)
    keys.take_nonce(by.user, DeviceNonce.Use.SIGN, nonce, f"votes.vote:{vote.id}")
    message = canonical_bytes(signing_payload(vote, nonce, choice))
    if hashlib.sha256(message).hexdigest() != payload_hash:
        raise Problem("payload_changed", "What you are voting on has changed. Look again.",
                      status.HTTP_409_CONFLICT)
    try:
        signature = base64.b64decode(signature_b64)
    except (ValueError, TypeError):
        signature = b""
    if not attestation.verify_signature(bytes(device.public_key_der), message, signature):
        raise Problem("signature_invalid", "The phone's signature did not check out.",
                      status.HTTP_400_BAD_REQUEST)
    return _record(vote, by, choice, method="device", device=device,
                   payload_hash=payload_hash, signature=signature)


# --- Recording, closing, counting ------------------------------------------------------------

def _record(vote: Vote, by: Membership, choice: str, *, method: str, device=None,
            payload_hash: str = "", signature: bytes | None = None) -> Vote:
    with transaction.atomic():
        vote = Vote.objects.select_for_update().get(pk=vote.pk)
        _require_can_vote(vote, by)
        secret = vote.secret
        # In a secret vote nothing that names the voter is stored with their choice: not
        # the member, and not the phone key or its signature.
        Ballot.objects.create(vote=vote, voter_hash=_voter_hash(vote, by),
                              membership=None if secret else by, choice=choice, method=method,
                              device=None if secret else device,
                              payload_hash="" if secret else payload_hash,
                              signature=None if secret else signature)
        Turnout.objects.create(vote=vote, membership=by)
        if Turnout.objects.filter(vote=vote).count() >= vote.eligible:
            _close(vote)
    vote.refresh_from_db()
    return vote


def _close(vote: Vote) -> Vote:
    """Count, check quorum and threshold, write the result to the ledger. Called inside a
    transaction holding the vote's row."""
    yes = vote.ballots.filter(choice=Ballot.Choice.YES).count()
    no = vote.ballots.filter(choice=Ballot.Choice.NO).count()
    cast = yes + no
    if cast < quorum_needed(vote):
        result = Vote.State.LAPSED
    elif yes * 100 > vote.pass_pct * cast:
        result = Vote.State.PASSED
    else:
        result = Vote.State.FAILED
    voters = sorted(Turnout.objects.filter(vote=vote)
                    .values_list("membership__member_code", flat=True))
    entry = ledger.append(
        chama=vote.chama, type="vote_result", source="vote", source_id=vote.id,
        decision={"subject": vote.subject, "question": vote.question, "result": result,
                  "yes": yes, "no": no, "eligible": vote.eligible,
                  "quorum_pct": vote.quorum_pct, "pass_pct": vote.pass_pct,
                  "secret": vote.secret, "voted": voters})
    vote.state, vote.yes, vote.no, vote.result_entry = result, yes, no, entry
    vote.save(update_fields=["state", "yes", "no", "result_entry"])
    _tell_subject(vote)
    return vote


def _tell_subject(vote: Vote) -> None:
    if vote.subject.startswith("payouts.proposal:"):
        from apps.payouts.services import on_vote_closed

        on_vote_closed(vote)
    elif vote.subject.startswith("rules.proposal:"):
        from apps.rules.proposals import on_vote_closed as rules_vote_closed

        rules_vote_closed(vote)


def close_due(chama: Chama | None = None) -> int:
    """Votes whose closing time has passed. Runs hourly and on every read."""
    qs = Vote.objects.filter(state=Vote.State.OPEN, closes_at__lte=timezone.now())
    if chama is not None:
        qs = qs.filter(chama=chama)
    n = 0
    for v in qs:
        with transaction.atomic():
            v = Vote.objects.select_for_update().get(pk=v.pk)
            if v.state == Vote.State.OPEN:
                _close(v)
                n += 1
    return n


def for_subject(subject: str) -> Vote | None:
    return Vote.objects.filter(subject=subject).order_by("-created_at").first()
