"""Fine waivers (5.9 steps 5 and 6). See docs/flows/fine-waivers.md.

A member asks, with a reason. The rules' ``waivers`` section sets how many officials must
approve (default 2) and whether members also vote. Officials approve with an SMS code that
names the member and the amount; nobody approves a waiver of their own fine, and the
member asking never decides. An approved waiver writes a fine_waived entry with the reason,
which every member sees.
"""

import hashlib
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status

from apps.chamas.models import OFFICIAL_ROLES, Membership
from apps.core.canonical import canonical_bytes
from apps.core.problems import Problem
from apps.devices import signing
from apps.devices.models import SigningChallenge
from apps.ledger import services as ledger
from apps.rules.models import version_at

from .models import Fine, Waiver, WaiverDecision

DEFAULTS = {"officials": 2, "member_vote": False}
REF_WAIVER = "3.5"


def settings_from(rules: dict) -> dict:
    return {**DEFAULTS, **(rules.get("waivers") or {})}


def _kes(cents: int) -> str:
    return f"KES {cents / 100:,.0f}" if cents % 100 == 0 else f"KES {cents / 100:,.2f}"


def _name(m: Membership) -> str:
    return m.user.display_name or m.member_code


def deciders(w: Waiver):
    """Officials who may decide: never the fined member, never the one asking."""
    return (Membership.objects.filter(chama=w.fine.chama, role__in=OFFICIAL_ROLES,
                                      state=Membership.State.ACTIVE)
            .exclude(pk=w.fine.membership_id).exclude(pk=w.requested_by_id))


def request(fine: Fine, by: Membership, *, reason: str) -> Waiver:
    if fine.membership_id != by.id:
        raise Problem("not_allowed", "Members ask for waivers of their own fines.",
                      status.HTTP_403_FORBIDDEN)
    if fine.outstanding_cents <= 0 or fine.state != Fine.State.RAISED:
        raise Problem("fine_not_open", "Only an unpaid fine can be waived.",
                      status.HTTP_409_CONFLICT)
    reason = (reason or "").strip()[:500]
    if not reason:
        raise Problem("reason_required", "Say why the fine should be waived.")
    version = version_at(fine.chama, timezone.localdate())
    s = settings_from(version.rules)
    with transaction.atomic():
        Fine.objects.select_for_update().get(pk=fine.pk)
        if fine.waivers.filter(state=Waiver.State.REQUESTED).exists():
            raise Problem("already_sent", "A waiver is already being decided.",
                          status.HTTP_409_CONFLICT)
        w = Waiver.objects.create(
            fine=fine, requested_by=by, reason=reason, needs_officials=s["officials"],
            needs_vote=s["member_vote"],
            deadline_at=timezone.now() + timedelta(
                hours=version.rules["approvals"]["deadline_hours"]))
        if deciders(w).count() < w.needs_officials:
            w.state, w.state_reason = (Waiver.State.REFUSED,
                                       "Not enough other officials to decide it.")
            w.save(update_fields=["state", "state_reason"])
            return w
        if w.needs_vote:
            from apps.votes import services as votes

            votes.open_vote(fine.chama, subject=f"fines.waiver:{w.id}",
                            question=f"Waive {_name(by)}'s fine of "
                                     f"{_kes(fine.outstanding_cents)}: {reason[:150]}?",
                            rules=version.rules, closes_at=w.deadline_at, exclude=[by.id])
    return w


def _hash(w: Waiver) -> str:
    return hashlib.sha256(canonical_bytes({
        "waiver": str(w.id), "fine": str(w.fine_id),
        "amount_cents": w.fine.outstanding_cents})).hexdigest()


def summary(w: Waiver, language: str) -> str:
    who, amount = _name(w.fine.membership), _kes(w.fine.outstanding_cents)
    if language == "sw":
        return f"KUSAMEHE faini ya {amount} ya {who}"
    return f"WAIVE {who}'s fine of {amount}"


def _require_decider(w: Waiver, by: Membership) -> None:
    if w.state != Waiver.State.REQUESTED or w.deadline_at <= timezone.now():
        raise Problem("already_answered", "This waiver is no longer being decided.",
                      status.HTTP_409_CONFLICT)
    if by.id in (w.fine.membership_id, w.requested_by_id):
        raise Problem("own_fine", "Nobody decides a waiver of their own fine.",
                      status.HTTP_403_FORBIDDEN)
    if not deciders(w).filter(pk=by.pk).exists():
        raise Problem("not_allowed", "Officials decide waivers.", status.HTTP_403_FORBIDDEN)
    if w.decisions.filter(membership=by).exists():
        raise Problem("already_answered", "You have already answered.",
                      status.HTTP_409_CONFLICT)


def start_approve(w: Waiver, by: Membership) -> SigningChallenge:
    _require_decider(w, by)
    return signing.issue(user=by.user, purpose=SigningChallenge.Purpose.APPROVE_WAIVER,
                         subject=f"fines.waiver:{w.id}", content_hash=_hash(w),
                         action_text=summary(w, by.user.language))


def approve(w: Waiver, by: Membership, *, challenge_id, sms_code: str) -> Waiver:
    _require_decider(w, by)
    signing.confirm(user=by.user, challenge_id=challenge_id, code=sms_code,
                    purpose=SigningChallenge.Purpose.APPROVE_WAIVER,
                    subject=f"fines.waiver:{w.id}", content_hash=_hash(w))
    with transaction.atomic():
        w = Waiver.objects.select_for_update().get(pk=w.pk)
        _require_decider(w, by)
        try:
            WaiverDecision.objects.create(waiver=w, membership=by,
                                          decision=WaiverDecision.Decision.APPROVE,
                                          challenge_id=challenge_id)
        except IntegrityError as exc:
            raise Problem("already_answered", "You have already answered.",
                          status.HTTP_409_CONFLICT) from exc
        _maybe_waive(w)
    w.refresh_from_db()
    return w


def decline(w: Waiver, by: Membership, *, reason: str = "") -> Waiver:
    _require_decider(w, by)
    with transaction.atomic():
        w = Waiver.objects.select_for_update().get(pk=w.pk)
        WaiverDecision.objects.create(waiver=w, membership=by,
                                      decision=WaiverDecision.Decision.DECLINE,
                                      reason=(reason or "").strip()[:300])
        declines = w.decisions.filter(decision=WaiverDecision.Decision.DECLINE).count()
        if deciders(w).count() - declines < w.needs_officials:
            w.state, w.state_reason = Waiver.State.REFUSED, "The officials declined it."
            w.save(update_fields=["state", "state_reason"])
    return w


def _maybe_waive(w: Waiver) -> None:
    """Called with the waiver locked."""
    if w.state != Waiver.State.REQUESTED:
        return
    if w.decisions.filter(decision=WaiverDecision.Decision.APPROVE).count() < w.needs_officials:
        return
    if w.needs_vote:
        from apps.votes.services import for_subject

        v = for_subject(f"fines.waiver:{w.id}")
        if v is None or v.state != "passed":
            return
    fine = Fine.objects.select_for_update().get(pk=w.fine_id)
    amount = fine.outstanding_cents
    if amount <= 0 or fine.state != Fine.State.RAISED:
        w.state, w.state_reason = Waiver.State.REFUSED, "The fine was paid meanwhile."
        w.save(update_fields=["state", "state_reason"])
        return
    approvers = [d.membership.member_code for d in
                 w.decisions.filter(decision=WaiverDecision.Decision.APPROVE)
                 .select_related("membership")]
    entry = ledger.append(
        chama=fine.chama, type="fine_waived", source="waiver", source_id=w.id,
        member=fine.membership, amount_cents=amount, period=fine.period,
        rule_version_id=version_at(fine.chama, timezone.localdate()).id,
        rule_refs=[REF_WAIVER, fine.rule_ref],
        decision={"fine_id": str(fine.id), "reason": w.reason, "approved_by": approvers,
                  "vote": w.needs_vote})
    fine.state = Fine.State.WAIVED
    fine.save(update_fields=["state"])
    w.state, w.entry = Waiver.State.WAIVED, entry
    w.save(update_fields=["state", "entry"])


def on_vote_closed(vote) -> None:
    w = Waiver.objects.select_for_update().get(pk=vote.subject.split(":", 1)[1])
    if vote.state == "passed":
        _maybe_waive(w)
    elif w.state == Waiver.State.REQUESTED:
        w.state = Waiver.State.REFUSED
        w.state_reason = ("The members voted no." if vote.state == "failed"
                          else "Not enough members voted.")
        w.save(update_fields=["state", "state_reason"])


def expire_due() -> int:
    from apps.votes.services import close_due

    close_due()
    n = 0
    for w in Waiver.objects.filter(state=Waiver.State.REQUESTED,
                                   deadline_at__lte=timezone.now()):
        w.state, w.state_reason = Waiver.State.EXPIRED, "Not decided in time."
        w.save(update_fields=["state", "state_reason"])
        n += 1
    return n
