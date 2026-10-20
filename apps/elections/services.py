"""Elections and role changes (5.12). See docs/flows/elections.md.

An official nominates a member for a role; the members vote. The winner takes the role
only once their phone key is active (and, later, the ID check), so the chama is never
without enough signers: the outgoing official keeps the role until then. When an official
leaves suddenly or dies, the others name an acting official at once, and a members' vote
confirms them within 30 days. A change of bank signatory reminds the officials to update
the bank's mandate and to confirm when it is done.
"""

import hashlib
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.chamas.models import OFFICIAL_ROLES, Chama, Membership
from apps.core.problems import Problem
from apps.ledger import services as ledger
from apps.notify.sms import SmsFailed, send_sms
from apps.rules.models import version_at

from .models import Election

DEFAULTS = {"term_months": 24, "vote_days": 7}
ACTING_DAYS = 30
SIGNATORY_ROLES = ("chair", "treasurer")
REF_ELECTIONS = "15.1"


def settings_from(rules: dict) -> dict:
    return {**DEFAULTS, **(rules.get("elections") or {})}


def _name(m: Membership) -> str:
    return m.user.display_name or m.member_code


def _holder(chama: Chama, role: str) -> Membership | None:
    return Membership.objects.filter(chama=chama, role=role,
                                     state=Membership.State.ACTIVE).first()


def _officials(chama: Chama):
    return Membership.objects.filter(chama=chama, role__in=OFFICIAL_ROLES,
                                     state=Membership.State.ACTIVE).select_related("user")


def nominate(chama: Chama, by: Membership, *, role: str, candidate_id,
             acting: bool = False) -> Election:
    if by.role not in OFFICIAL_ROLES:
        raise Problem("not_allowed", "An official calls an election.",
                      status.HTTP_403_FORBIDDEN)
    version = version_at(chama, timezone.localdate())
    if role not in version.rules["officials"]["roles"]:
        raise Problem("invalid", "This chama's rules have no such role.")
    candidate = Membership.objects.filter(chama=chama, pk=candidate_id,
                                          state=Membership.State.ACTIVE) \
        .select_related("user").first()
    if candidate is None:
        raise Problem("invalid_member", "Choose an active member of this chama.")
    if candidate.role == role:
        raise Problem("invalid_member", "They already hold this role.")
    if candidate.role in OFFICIAL_ROLES:
        raise Problem("invalid_member", "They already hold another official role.")
    if Election.objects.filter(chama=chama, role=role,
                               state__in=[Election.State.VOTING, Election.State.WON,
                                          Election.State.ACTING]).exists():
        raise Problem("election_open", "An election for this role is already under way.",
                      status.HTTP_409_CONFLICT)
    outgoing = _holder(chama, role)
    if acting and outgoing is not None:
        raise Problem("role_filled", "Someone holds this role; hold an election instead.",
                      status.HTTP_409_CONFLICT)
    s = settings_from(version.rules)
    from apps.votes import services as votes

    with transaction.atomic():
        e = Election.objects.create(chama=chama, role=role, candidate=candidate,
                                    outgoing=outgoing, proposed_by=by, acting=acting,
                                    term_months=s["term_months"])
        days = ACTING_DAYS if acting else s["vote_days"]
        question = (f"Confirm {_name(candidate)} as acting {role}?" if acting
                    else f"Elect {_name(candidate)} as {role} for {s['term_months']} months?")
        v = votes.open_vote(chama, subject=f"elections.election:{e.id}", question=question,
                            rules=version.rules,
                            closes_at=timezone.now() + timedelta(days=days))
        e.vote_id = v.id
        e.save(update_fields=["vote_id"])
        if acting:
            _install(e, acting=True)
    return e


def _set_role(m: Membership, role: str) -> None:
    Membership.objects.filter(pk=m.pk).update(role=role)
    m.role = role


def _install(e: Election, *, acting: bool = False) -> None:
    """The candidate takes the role; the outgoing official becomes a member."""
    if e.outgoing_id:
        _set_role(e.outgoing, Membership.Role.MEMBER)
    _set_role(e.candidate, e.role)
    signatory = e.role in SIGNATORY_ROLES
    e.role_entry = ledger.append(
        chama=e.chama, type="role_changed", source="election", source_id=e.id,
        member=e.candidate, rule_refs=[REF_ELECTIONS],
        decision={"role": e.role, "acting": acting,
                  "outgoing": e.outgoing.member_code if e.outgoing_id else None,
                  "term_months": e.term_months, "vote": str(e.vote_id)})
    e.state = Election.State.ACTING if acting else Election.State.IN_OFFICE
    e.mandate_needed = signatory
    e.save(update_fields=["role_entry", "state", "mandate_needed"])
    if signatory:
        transaction.on_commit(lambda: _remind_mandate(e.pk))


def _remind_mandate(election_id) -> None:
    e = Election.objects.select_related("chama", "candidate__user").get(pk=election_id)
    for m in _officials(e.chama):
        text = {"en": f"{e.chama.name}: {_name(e.candidate)} is now {e.role}. Update the "
                      "signatories at the bank, then confirm it in the app.",
                "sw": f"{e.chama.name}: {_name(e.candidate)} sasa ni {e.role}. Badilisha "
                      "watia sahihi benki, kisha thibitisha kwenye programu."}
        try:
            send_sms(m.user.phone, text.get(m.user.language, text["en"]))
        except SmsFailed:
            continue


def ready(m: Membership) -> bool:
    """A new official needs an active phone key before the role starts (and the ID check,
    5.15, once that exists)."""
    from apps.devices.keys import active_device

    return active_device(m.user) is not None


def try_install(e: Election) -> bool:
    if e.state != Election.State.WON or not ready(e.candidate):
        return False
    _install(e)
    return True


def on_vote_closed(vote) -> None:
    e = Election.objects.select_for_update(of=("self",)).select_related(
        "chama", "candidate__user", "outgoing").get(pk=vote.subject.split(":", 1)[1])
    tied = vote.state == "failed" and vote.yes == vote.no and (vote.yes + vote.no) > 0
    if tied and e.state == Election.State.VOTING:
        if e.tie_round == 0:
            # A tie: the members vote once more, on the same question.
            _rerun(e, vote)
        else:
            # Tied twice: a public lot decides, drawn from the next night's anchor (a
            # number nobody can know in advance or change afterwards).
            e.state, e.lot_after = Election.State.AWAITING_LOT, timezone.now()
            e.save(update_fields=["state", "lot_after"])
        return
    if vote.state == "passed":
        if e.state == Election.State.VOTING:
            e.state = Election.State.WON
            e.save(update_fields=["state"])
            try_install(e)
        elif e.state == Election.State.ACTING:
            e.state = Election.State.IN_OFFICE  # confirmed
            e.save(update_fields=["state"])
        return
    if e.state == Election.State.ACTING:
        # Not confirmed: the acting official steps down.
        _set_role(e.candidate, Membership.Role.MEMBER)
        ledger.append(chama=e.chama, type="role_changed", source="election", source_id=e.id,
                      member=e.candidate, rule_refs=[REF_ELECTIONS],
                      decision={"role": Membership.Role.MEMBER, "was": e.role,
                                "reason": "acting official not confirmed by the members"})
    if e.state in (Election.State.VOTING, Election.State.ACTING):
        e.state = Election.State.LOST
        e.save(update_fields=["state"])


def _rerun(e: Election, tied_vote) -> None:
    from apps.votes import services as votes

    version = version_at(e.chama, timezone.localdate())
    s = settings_from(version.rules)
    v = votes.open_vote(e.chama, subject=f"elections.election:{e.id}",
                        question=f"Tied. Vote again: {tied_vote.question}"[:300],
                        rules=version.rules,
                        closes_at=timezone.now() + timedelta(days=s["vote_days"]))
    e.tie_round, e.vote_id = 1, v.id
    e.save(update_fields=["tie_round", "vote_id"])


def lot_draw(merkle_root: str, election_id) -> str:
    """The public lot: SHA-256 of that night's anchor root and the election. Anyone can
    recompute it from the published root."""
    return hashlib.sha256(f"{merkle_root}:{election_id}".encode()).hexdigest()


def draw_lots(anchor) -> int:
    """Called right after each night's anchor: every election tied twice before it is
    decided by its draw. Even last digit: elected; odd: not elected."""
    done = 0
    for e in Election.objects.filter(state=Election.State.AWAITING_LOT,
                                     lot_after__lt=anchor.at).select_related("chama",
                                                                             "candidate"):
        draw = lot_draw(anchor.merkle_root, e.id)
        elected = int(draw[-1], 16) % 2 == 0
        with transaction.atomic():
            e = Election.objects.select_for_update().get(pk=e.pk)
            if e.state != Election.State.AWAITING_LOT:
                continue
            ledger.append(chama=e.chama, type="vote_result", source="election",
                          source_id=e.id, member=e.candidate, rule_refs=[REF_ELECTIONS],
                          decision={"lot": True, "role": e.role, "anchor_id": str(anchor.id),
                                    "merkle_root": anchor.merkle_root, "draw": draw,
                                    "rule": "even last digit elects", "elected": elected})
            e.state = Election.State.WON if elected else Election.State.LOST
            e.save(update_fields=["state"])
            if elected:
                try_install(e)
        done += 1
    return done


def install_ready(chama: Chama | None = None) -> int:
    """Winners whose key is now active take their role (daily, and on every read)."""
    qs = Election.objects.filter(state=Election.State.WON).select_related(
        "chama", "candidate__user", "outgoing")
    if chama is not None:
        qs = qs.filter(chama=chama)
    n = 0
    for e in qs:
        with transaction.atomic():
            if try_install(Election.objects.select_for_update().get(pk=e.pk)):
                n += 1
    return n


def confirm_mandate(e: Election, by: Membership) -> Election:
    if by.role not in OFFICIAL_ROLES or by.chama_id != e.chama_id:
        raise Problem("not_allowed", "An official confirms the bank mandate.",
                      status.HTTP_403_FORBIDDEN)
    if not e.mandate_needed or e.mandate_confirmed_at:
        raise Problem("already_answered", "Nothing to confirm.", status.HTTP_409_CONFLICT)
    e.mandate_confirmed_by, e.mandate_confirmed_at = by, timezone.now()
    e.save(update_fields=["mandate_confirmed_by", "mandate_confirmed_at"])
    ledger.append(chama=e.chama, type="role_changed", source="election", source_id=e.id,
                  member=e.candidate, rule_refs=[REF_ELECTIONS],
                  decision={"role": e.role, "bank_mandate_updated": True,
                            "confirmed_by": by.member_code})
    return e


def needs_election(chama: Chama) -> list[dict]:
    """Approval levels that the officials still in place can no longer reach: money out
    at that level is refused until an election."""
    version = version_at(chama, timezone.localdate())
    if version is None:
        return []
    n = _officials(chama).count()
    return [{"level": f"4.{i}", "officials": t["officials"], "available": n - 1}
            for i, t in enumerate(version.rules["approvals"]["tiers"], start=1)
            if t["officials"] > n - 1]
