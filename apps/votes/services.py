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
from datetime import timedelta

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

from .models import Ballot, Motion, MotionSecond, Turnout, Vote, VoteComment, VoteSmsNumber

DEFAULTS = {"quorum_pct": 50, "pass_pct": 50, "secret": False, "allow_change": False,
            "motion_seconders": 2}
MAX_OPTIONS = 6

# Below this many eligible voters a vote passes only if every one of them says yes: in a
# tiny group a "majority" is one or two people (owner decision 2026-10-20).
MIN_VOTERS = 5


def settings_from(rules: dict) -> dict:
    return {**DEFAULTS, **(rules.get("votes") or {})}


def open_vote(chama: Chama, *, subject: str, question: str, rules: dict, closes_at,
              exclude: list | None = None, quorum_pct: int | None = None,
              pass_pct: int | None = None, over_all: bool = False, kind: str = "yes_no",
              options: list | None = None, details: str = "", allow_change: bool | None = None,
              raised_by: Membership | None = None) -> Vote:
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
                               secret=s["secret"], over_all=over_all,
                               eligible=len(eligible),
                               eligible_ids=eligible, closes_at=closes_at, kind=kind,
                               options=list(options or []), details=details[:4000],
                               allow_change=(s["allow_change"] if allow_change is None
                                             else allow_change),
                               raised_by=raised_by)
    transaction.on_commit(lambda: sms_invites(vote.pk))
    transaction.on_commit(lambda: app_invites(vote.pk))
    return vote


def app_invites(vote_id) -> int:
    """Every eligible member sees the new vote in the app's notices (richer voting)."""
    from apps.automation.notices import notify

    vote = Vote.objects.select_related("chama").get(pk=vote_id)
    by = timezone.localtime(vote.closes_at).strftime("%d %b %H:%M")
    n = 0
    for m in Membership.objects.filter(pk__in=vote.eligible_ids).select_related("user"):
        if notify(m.user, chama=vote.chama, kind="vote", key=f"vote-open:{vote.id}",
                  sms="never", data={"screen": "vote", "id": str(vote.id)},
                  text={"en": f"New vote: {vote.question} Vote by {by}.",
                        "sw": f"Kura mpya: {vote.question} Piga kura kabla ya {by}."}):
            n += 1
    return n


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
        sw = m.user.language == "sw"
        if vote.kind == Vote.Kind.CHOICE:
            listed = ", ".join(f"{i} {n.number} ({o})"
                               for i, o in enumerate(vote.options, start=1))
            text = (f"{vote.chama.name} kura: {vote.question} Jibu {listed} kabla ya {by}."
                    if sw else
                    f"{vote.chama.name} vote: {vote.question} Reply {listed} by {by}.")
        elif sw:
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
    return _record(n.vote, n.membership, _choice(n.vote, choice), method="reply")


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
    if not vote.allow_change and Turnout.objects.filter(vote=vote, membership=by).exists():
        raise Problem("already_voted", "You have already voted. The first vote counts.",
                      status.HTTP_409_CONFLICT)


def _choice(vote: Vote, raw: str) -> str:
    raw = str(raw or "").strip().lower()
    if raw == Ballot.Choice.ABSTAIN:
        return raw
    if vote.kind == Vote.Kind.CHOICE:
        if raw.isdigit() and 1 <= int(raw) <= len(vote.options):
            return str(int(raw))
        raise Problem("choice_invalid", "Choose one of the options, or abstain.")
    if raw not in (Ballot.Choice.YES, Ballot.Choice.NO):
        raise Problem("choice_invalid", "Vote yes, no or abstain.")
    return raw


def latest_ballots(vote: Vote) -> list[Ballot]:
    """Each voter's ballot that counts: the highest round (a changed vote)."""
    best: dict[str, Ballot] = {}
    for b in vote.ballots.all():
        if b.voter_hash not in best or b.round > best[b.voter_hash].round:
            best[b.voter_hash] = b
    return list(best.values())


def my_ballot(vote: Vote, by: Membership) -> Ballot | None:
    return (vote.ballots.filter(voter_hash=_voter_hash(vote, by)).order_by("-round")
            .first())


def choice_word(vote: Vote, choice: str, lang: str) -> str:
    words = {"en": {"yes": "YES", "no": "NO", "abstain": "ABSTAIN"},
             "sw": {"yes": "NDIYO", "no": "HAPANA", "abstain": "NAJIZUIA"}}[lang]
    if choice in words:
        return words[choice]
    return f"{choice} ({vote.options[int(choice) - 1]})"


def _content_hash(vote: Vote, choice: str) -> str:
    return hashlib.sha256(canonical_bytes({"vote": str(vote.id), "choice": choice})).hexdigest()


# --- By SMS code ---------------------------------------------------------------------------

def start_sms_ballot(vote: Vote, by: Membership, choice: str) -> SigningChallenge:
    _require_can_vote(vote, by)
    choice = _choice(vote, choice)
    if keys.active_device(by.user) is not None:
        raise Problem("use_device_key", "This phone is set up for signing: vote with your "
                                        "fingerprint in the app.", status.HTTP_409_CONFLICT)
    lang = "sw" if by.user.language == "sw" else "en"
    action = (f"VOTE {choice_word(vote, choice, 'en')}: {vote.question}" if lang == "en"
              else f"KUPIGA KURA {choice_word(vote, choice, 'sw')}: {vote.question}")
    return signing.issue(user=by.user, purpose=SigningChallenge.Purpose.CAST_BALLOT,
                         subject=f"votes.vote:{vote.id}", content_hash=_content_hash(vote, choice),
                         action_text=action)


def cast_sms(vote: Vote, by: Membership, *, choice: str, challenge_id, sms_code: str) -> Vote:
    _require_can_vote(vote, by)
    choice = _choice(vote, choice)
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
    choice = _choice(vote, choice)
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
    choice = _choice(vote, choice)
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
        h = _voter_hash(vote, by)
        previous = vote.ballots.filter(voter_hash=h).count()
        Ballot.objects.create(vote=vote, voter_hash=h, round=previous + 1,
                              membership=None if secret else by, choice=choice, method=method,
                              device=None if secret else device,
                              payload_hash="" if secret else payload_hash,
                              signature=None if secret else signature)
        if not previous:
            Turnout.objects.create(vote=vote, membership=by)
        if Turnout.objects.filter(vote=vote).count() >= vote.eligible:
            _close(vote)
    vote.refresh_from_db()
    return vote


def _close(vote: Vote) -> Vote:
    """Count, check quorum and threshold, write the result to the ledger. Called inside a
    transaction holding the vote's row. Abstaining counts as taking part (quorum) but
    neither for nor against; a poll is won by the one option with the most votes."""
    ballots = latest_ballots(vote)
    yes = sum(1 for b in ballots if b.choice == Ballot.Choice.YES)
    no = sum(1 for b in ballots if b.choice == Ballot.Choice.NO)
    abstain = sum(1 for b in ballots if b.choice == Ballot.Choice.ABSTAIN)
    cast = len(ballots)
    counts, winner = {}, None
    small = vote.eligible < MIN_VOTERS and vote.kind == Vote.Kind.YES_NO
    if vote.kind == Vote.Kind.CHOICE:
        counts = {str(i): sum(1 for b in ballots if b.choice == str(i))
                  for i in range(1, len(vote.options) + 1)}
        top = max(counts.values(), default=0)
        leaders = [int(k) for k, c in counts.items() if c == top and top > 0]
        if cast < quorum_needed(vote):
            result = Vote.State.LAPSED
        elif len(leaders) == 1:
            result, winner = Vote.State.PASSED, leaders[0]
        else:
            result = Vote.State.FAILED  # a tie at the top, or everyone abstained
        tied = len(leaders) > 1
    else:
        if small:
            # A tiny group decides together: everyone eligible must say yes.
            result = Vote.State.PASSED if yes == vote.eligible else (
                Vote.State.LAPSED if cast < vote.eligible and no == 0 else Vote.State.FAILED)
        elif cast < quorum_needed(vote):
            result = Vote.State.LAPSED
        elif yes * 100 > vote.pass_pct * (vote.eligible if vote.over_all else yes + no):
            result = Vote.State.PASSED
        else:
            result = Vote.State.FAILED
        # A tie never changes anything: it is recorded as a tie so everyone sees why.
        tied = result == Vote.State.FAILED and yes == no and yes + no > 0
    voters = sorted(Turnout.objects.filter(vote=vote)
                    .values_list("membership__member_code", flat=True))
    decision = {"subject": vote.subject, "question": vote.question, "result": result,
                "yes": yes, "no": no, "eligible": vote.eligible,
                "quorum_pct": vote.quorum_pct, "pass_pct": vote.pass_pct,
                "over_all": vote.over_all, "unanimous_needed": small, "tied": tied,
                "secret": vote.secret, "voted": voters}
    if abstain:
        decision["abstain"] = abstain
    if vote.kind == Vote.Kind.CHOICE:
        decision.update(kind="choice", options=vote.options, option_counts=counts,
                        winner=winner)
    if vote.raised_by_id:
        decision["raised_by"] = vote.raised_by.member_code
    entry = ledger.append(chama=vote.chama, type="vote_result", source="vote",
                          source_id=vote.id, decision=decision)
    vote.state, vote.yes, vote.no, vote.result_entry = result, yes, no, entry
    vote.abstain, vote.option_counts, vote.winner = abstain, counts, winner
    vote.save(update_fields=["state", "yes", "no", "abstain", "option_counts", "winner",
                             "result_entry"])
    _tell_subject(vote)
    return vote


def _tell_subject(vote: Vote) -> None:
    if vote.subject.startswith("payouts.proposal:"):
        from apps.payouts.services import on_vote_closed

        on_vote_closed(vote)
    elif vote.subject.startswith("payouts.debit:"):
        from apps.payouts.services import on_debit_vote_closed

        on_debit_vote_closed(vote)
    elif vote.subject.startswith("referrals.referral:"):
        from apps.referrals.services import on_vote_closed as referral_vote_closed

        referral_vote_closed(vote)
    elif vote.subject.startswith("elections.election:"):
        from apps.elections.services import on_vote_closed as election_vote_closed

        election_vote_closed(vote)
    elif vote.subject.startswith("device:"):
        from apps.devices.changes import on_vote_closed as device_vote_closed

        device_vote_closed(vote)
    elif vote.subject.startswith("disputes.dispute:"):
        from apps.disputes.services import on_vote_closed as dispute_vote_closed

        dispute_vote_closed(vote)
    elif vote.subject.startswith("fines.waiver:"):
        from apps.fines.waivers import on_vote_closed as waiver_vote_closed

        waiver_vote_closed(vote)
    elif vote.subject.startswith("rules.proposal:"):
        from apps.rules.proposals import on_vote_closed as rules_vote_closed

        rules_vote_closed(vote)
    elif vote.subject.startswith("votes.motion:"):
        motion_vote_closed(vote)
    elif vote.subject.startswith("shareout.shareout:"):
        from apps.shareout.services import on_vote_closed as shareout_vote_closed

        shareout_vote_closed(vote)


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


# --- Comments ------------------------------------------------------------------------------

def comment(vote: Vote, by: Membership, text: str) -> VoteComment:
    """A member's word on an open vote, for everyone to read before voting."""
    text = (text or "").strip()
    if not text:
        raise Problem("text_required", "Write something first.")
    if by.chama_id != vote.chama_id or not by.is_current:
        raise Problem("not_allowed", "Only members of this chama comment.",
                      status.HTTP_403_FORBIDDEN)
    close_due(vote.chama)
    vote.refresh_from_db()
    if vote.state != Vote.State.OPEN:
        raise Problem("vote_closed", "This vote has closed.", status.HTTP_409_CONFLICT)
    return VoteComment.objects.create(vote=vote, membership=by, text=text[:1000])


# --- Members' motions ------------------------------------------------------------------------

SECOND_DAYS = 7  # a motion not seconded within a week expires
MAX_SECONDING = 2  # motions one member may have waiting for seconds at once


def _clean_options(kind: str, options) -> list[str]:
    if kind != Vote.Kind.CHOICE:
        return []
    cleaned = [str(o).strip()[:80] for o in (options or []) if str(o).strip()]
    if not 2 <= len(cleaned) <= MAX_OPTIONS or len(set(o.lower() for o in cleaned)) != len(
            cleaned):
        raise Problem("options_invalid", f"Give 2 to {MAX_OPTIONS} different options.")
    return cleaned


def raise_motion(chama: Chama, by: Membership, *, question: str, details: str = "",
                 kind: str = "yes_no", options=None, vote_days: int = 7) -> Motion:
    """Any member may ask the chama something: yes or no, or a poll."""
    from apps.rules.models import version_at

    if by.chama_id != chama.id or by.state != Membership.State.ACTIVE:
        raise Problem("not_allowed", "Only active members raise motions.",
                      status.HTTP_403_FORBIDDEN)
    question = (question or "").strip()
    if len(question) < 5:
        raise Problem("question_required", "Write the question.")
    if kind not in Vote.Kind.values:
        raise Problem("invalid", "Choose yes or no, or a poll.")
    if not 1 <= int(vote_days) <= 14:
        raise Problem("invalid", "A vote runs 1 to 14 days.")
    if Motion.objects.filter(chama=chama, raised_by=by,
                             state=Motion.State.SECONDING).count() >= MAX_SECONDING:
        raise Problem("too_many_motions", "Wait until your other motions are seconded.",
                      status.HTTP_409_CONFLICT)
    version = version_at(chama, timezone.localdate())
    needed = settings_from(version.rules if version else {})["motion_seconders"]
    motion = Motion.objects.create(
        chama=chama, raised_by=by, kind=kind, question=question[:300],
        details=(details or "").strip()[:4000], options=_clean_options(kind, options),
        vote_days=int(vote_days), seconds_needed=needed,
        second_by=timezone.now() + timedelta(days=SECOND_DAYS))
    if needed == 0:
        _open_motion_vote(motion)
    else:
        transaction.on_commit(lambda: _ask_for_seconds(motion.pk))
    return motion


def _ask_for_seconds(motion_id) -> int:
    from apps.automation.notices import notify

    motion = Motion.objects.select_related("chama", "raised_by__user").get(pk=motion_id)
    who = motion.raised_by.user.display_name or motion.raised_by.member_code
    n = 0
    for m in (Membership.objects.filter(chama=motion.chama, state=Membership.State.ACTIVE)
              .exclude(pk=motion.raised_by_id).select_related("user")):
        if notify(m.user, chama=motion.chama, kind="motion", key=f"motion:{motion.id}",
                  sms="never", data={"screen": "motion", "id": str(motion.id)},
                  text={"en": f"{who} asks the chama: {motion.question} Second it in "
                              f"JiraniChama to put it to a vote.",
                        "sw": f"{who} anauliza chama: {motion.question} Liunge mkono kwenye "
                              f"JiraniChama ili lipigiwe kura."}):
            n += 1
    return n


def second(motion: Motion, by: Membership) -> Motion:
    with transaction.atomic():
        motion = Motion.objects.select_for_update().get(pk=motion.pk)
        expire_motions(motion.chama)
        motion.refresh_from_db()
        if by.chama_id != motion.chama_id or by.state != Membership.State.ACTIVE:
            raise Problem("not_allowed", "Only active members second motions.",
                          status.HTTP_403_FORBIDDEN)
        if motion.state != Motion.State.SECONDING:
            raise Problem("not_seconding", "This motion is not waiting for seconds.",
                          status.HTTP_409_CONFLICT)
        if by.id == motion.raised_by_id:
            raise Problem("own_motion", "Someone else seconds your motion.",
                          status.HTTP_409_CONFLICT)
        _, made = MotionSecond.objects.get_or_create(motion=motion, membership=by)
        if not made:
            raise Problem("already_seconded", "You have seconded this already.",
                          status.HTTP_409_CONFLICT)
        if motion.seconds.count() >= motion.seconds_needed:
            _open_motion_vote(motion)
    motion.refresh_from_db()
    return motion


def _open_motion_vote(motion: Motion) -> None:
    from apps.rules.models import version_at

    version = version_at(motion.chama, timezone.localdate())
    vote = open_vote(motion.chama, subject=f"votes.motion:{motion.id}",
                     question=motion.question, rules=version.rules if version else {},
                     closes_at=timezone.now() + timedelta(days=motion.vote_days),
                     kind=motion.kind, options=motion.options, details=motion.details,
                     allow_change=True, raised_by=motion.raised_by)
    motion.state, motion.vote = Motion.State.VOTING, vote
    motion.save(update_fields=["state", "vote"])


def withdraw_motion(motion: Motion, by: Membership) -> Motion:
    if by.id != motion.raised_by_id:
        raise Problem("not_allowed", "Only the member who raised it withdraws it.",
                      status.HTTP_403_FORBIDDEN)
    if motion.state != Motion.State.SECONDING:
        raise Problem("not_seconding", "Once voting starts, a motion runs to the end.",
                      status.HTTP_409_CONFLICT)
    motion.state = Motion.State.WITHDRAWN
    motion.save(update_fields=["state"])
    return motion


def motion_vote_closed(vote: Vote) -> None:
    motion = Motion.objects.select_for_update().get(pk=vote.subject.split(":", 1)[1])
    motion.state = {Vote.State.PASSED: Motion.State.PASSED,
                    Vote.State.FAILED: Motion.State.FAILED}.get(vote.state, Motion.State.LAPSED)
    motion.save(update_fields=["state"])


def expire_motions(chama: Chama | None = None) -> int:
    qs = Motion.objects.filter(state=Motion.State.SECONDING, second_by__lte=timezone.now())
    if chama is not None:
        qs = qs.filter(chama=chama)
    return qs.update(state=Motion.State.EXPIRED)
