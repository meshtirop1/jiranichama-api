"""Disputes (5.14). See docs/flows/disputes.md.

Any member raises a problem about an entry, a fine, a payout or loan, a bank payment,
minutes, or a rule outcome. Everyone sees it (unless the rules let it be officials-only).
Nothing is frozen except money out still waiting for approval, which pauses. The people
involved have 72 hours to respond. Then the chama decides in the way its rules set:
officials agree on an outcome by SMS code, or the members vote on it. The outcome is
written as ledger entries. JiraniChama staff never decide; they can only hand over the
evidence pack.
"""

import hashlib
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.chamas.models import OFFICIAL_ROLES, Chama, Membership
from apps.core import money
from apps.core.canonical import canonical_bytes
from apps.core.problems import Problem
from apps.devices import signing
from apps.devices.models import SigningChallenge
from apps.ledger import services as ledger
from apps.ledger.models import LedgerEntry
from apps.notify.sms import SmsFailed, send_sms
from apps.rules.models import version_at

from .models import Dispute, DisputeMessage

DEFAULTS = {"decided_by": "officials", "officials": 2, "response_hours": 72,
            "allow_officials_only": True}
REF_DISPUTES = "14.1"


def settings_from(rules: dict) -> dict:
    return {**DEFAULTS, **(rules.get("disputes") or {})}


def _rules(chama: Chama) -> dict:
    return settings_from(version_at(chama, timezone.localdate()).rules)


def _name(m: Membership | None) -> str:
    if m is None:
        return "JiraniChama"
    return m.user.display_name or m.member_code


def _kes(cents: int, of=None) -> str:
    return money.fmt(cents, of)


def _officials(chama: Chama):
    return Membership.objects.filter(chama=chama, role__in=OFFICIAL_ROLES,
                                     state=Membership.State.ACTIVE).select_related("user")


# --- the subject and who is involved -------------------------------------------------------

def _subject(chama: Chama, kind: str, subject_id):
    """The disputed thing, checked to belong to this chama, and who is involved in it."""
    from apps.feeds.models import BankTransaction
    from apps.fines.models import Fine
    from apps.meetings.models import Minutes
    from apps.payouts.models import Approval, Proposal
    from apps.reconciliation.models import Match

    if kind == Dispute.Subject.OTHER:
        return None, set()
    obj, involved = None, set()
    if kind == Dispute.Subject.ENTRY:
        obj = LedgerEntry.objects.filter(chama=chama, pk=subject_id).first()
        if obj and obj.member_id:
            involved.add(obj.member_id)
    elif kind == Dispute.Subject.FINE:
        obj = Fine.objects.filter(chama=chama, pk=subject_id).first()
        if obj:
            involved.add(obj.membership_id)
    elif kind == Dispute.Subject.PROPOSAL:
        obj = Proposal.objects.filter(chama=chama, pk=subject_id).first()
        if obj:
            involved |= {obj.proposed_by_id, obj.payee_membership_id}
            involved |= set(Approval.objects.filter(proposal=obj)
                            .values_list("membership_id", flat=True))
    elif kind == Dispute.Subject.PAYMENT:
        obj = BankTransaction.objects.filter(chama=chama, pk=subject_id).first()
        if obj:
            involved |= set(Match.objects.filter(bank_txn=obj)
                            .values_list("membership_id", flat=True))
            involved |= set(_officials(chama).filter(role="treasurer")
                            .values_list("id", flat=True))
    elif kind == Dispute.Subject.MINUTES:
        obj = Minutes.objects.filter(meeting__chama=chama, pk=subject_id).first()
        if obj:
            involved.add(obj.written_by_id)
    if obj is None:
        raise Problem("subject_not_found", "That record is not in this chama.",
                      status.HTTP_404_NOT_FOUND)
    involved.discard(None)
    return obj, involved


def raise_dispute(chama: Chama, by: Membership, *, subject_type: str, subject_id=None,
                  reason: str, text: str, officials_only: bool = False) -> Dispute:
    text = (text or "").strip()
    if not text:
        raise Problem("reason_required", "Say what you think is wrong.")
    if reason not in Dispute.Reason.values or subject_type not in Dispute.Subject.values:
        raise Problem("invalid", "Choose what the problem is about and why.")
    s = _rules(chama)
    if officials_only and not s["allow_officials_only"]:
        raise Problem("not_allowed", "This chama's disputes are open to every member.",
                      status.HTTP_403_FORBIDDEN)
    _, involved = _subject(chama, subject_type, subject_id)
    if not involved:
        involved = set(_officials(chama).values_list("id", flat=True))
    involved.discard(by.id)
    d = Dispute.objects.create(
        chama=chama, raised_by=by, subject_type=subject_type, subject_id=subject_id,
        reason=reason, text=text[:4000], officials_only=officials_only,
        involved=sorted(str(i) for i in involved),
        respond_until=timezone.now() + timedelta(hours=s["response_hours"]))
    transaction.on_commit(lambda: _tell_involved(d.pk))
    return d


def raise_automatic(chama: Chama, *, subject_type: str, subject_id, reason: str, text: str
                    ) -> Dispute:
    """A dispute JiraniChama opens by itself (automatic chasing, 2026-10-21): money that
    left the bank with no approval and no explanation. The bank signatories are involved
    (they are the ones who can withdraw), or the officials when no mandate is on record."""
    from apps.chamas.models import BankMandate

    s = _rules(chama)
    mandate = (BankMandate.objects.filter(chama=chama, state=BankMandate.State.CONFIRMED)
               .order_by("-confirmed_at").first())
    involved = (set(mandate.signatories.values_list("id", flat=True)) if mandate
                else set(_officials(chama).values_list("id", flat=True)))
    d = Dispute.objects.create(
        chama=chama, raised_by=None, automatic=True, subject_type=subject_type,
        subject_id=subject_id, reason=reason, text=text[:4000],
        involved=sorted(str(i) for i in involved),
        respond_until=timezone.now() + timedelta(hours=s["response_hours"]))
    transaction.on_commit(lambda: _tell_involved(d.pk))
    return d


def _tell_involved(dispute_id) -> None:
    d = Dispute.objects.select_related("chama", "raised_by__user").get(pk=dispute_id)
    hours = _rules(d.chama)["response_hours"]
    for m in Membership.objects.filter(pk__in=d.involved).select_related("user"):
        text = {"en": f"{d.chama.name}: {_name(d.raised_by)} raised a problem that involves "
                      f"you. Respond in the app within {hours} hours.",
                "sw": f"{d.chama.name}: {_name(d.raised_by)} ameleta tatizo linalokuhusu. "
                      f"Jibu kwenye programu ndani ya saa {hours}."}
        try:
            send_sms(m.user.phone, text.get(m.user.language, text["en"]))
        except SmsFailed:
            continue


def can_see(d: Dispute, m: Membership) -> bool:
    if not d.officials_only:
        return True
    return (m.id == d.raised_by_id or str(m.id) in d.involved
            or m.role in OFFICIAL_ROLES)


def visible(chama: Chama, m: Membership):
    qs = Dispute.objects.filter(chama=chama)
    return [d for d in qs.order_by("-created_at")[:100] if can_see(d, m)]


def open_on_proposal(proposal_id) -> bool:
    """Money out still waiting for approval pauses while a dispute about it is open."""
    return Dispute.objects.filter(subject_type=Dispute.Subject.PROPOSAL,
                                  subject_id=proposal_id, state__in=Dispute.OPEN).exists()


# --- responding, withdrawing, escalating ---------------------------------------------------

def _require_open(d: Dispute) -> None:
    if d.state not in Dispute.OPEN:
        raise Problem("dispute_closed", "This dispute is closed.", status.HTTP_409_CONFLICT)


def respond(d: Dispute, by: Membership, *, text: str) -> DisputeMessage:
    _require_open(d)
    if not can_see(d, by):
        raise Problem("not_found", "Dispute not found.", status.HTTP_404_NOT_FOUND)
    text = (text or "").strip()
    if not text:
        raise Problem("reason_required", "Write your response.")
    kind = (DisputeMessage.Kind.RESPONSE if str(by.id) in d.involved
            else DisputeMessage.Kind.NOTE)
    return DisputeMessage.objects.create(dispute=d, membership=by, kind=kind,
                                         text=text[:4000], round=d.round)


def withdraw(d: Dispute, by: Membership, *, text: str = "") -> Dispute:
    _require_open(d)
    if by.id != d.raised_by_id:
        raise Problem("not_allowed", "Only the member who raised it withdraws it.",
                      status.HTTP_403_FORBIDDEN)
    DisputeMessage.objects.create(dispute=d, membership=by, kind=DisputeMessage.Kind.NOTE,
                                  text=("Withdrawn. " + (text or "")).strip(), round=d.round)
    d.state = Dispute.State.WITHDRAWN
    d.save(update_fields=["state"])
    return d


def escalate(d: Dispute, by: Membership, *, text: str) -> Dispute:
    """Taken outside the chama (mediation, Small Claims Court, police), with the evidence
    pack. The records stay; nothing is decided in the app."""
    _require_open(d)
    if by.id != d.raised_by_id and by.role not in OFFICIAL_ROLES:
        raise Problem("not_allowed", "The member who raised it, or an official, escalates.",
                      status.HTTP_403_FORBIDDEN)
    text = (text or "").strip()
    if not text:
        raise Problem("reason_required", "Say where it is being taken.")
    DisputeMessage.objects.create(dispute=d, membership=by, kind=DisputeMessage.Kind.NOTE,
                                  text=f"Taken outside the chama: {text}"[:4000],
                                  round=d.round)
    d.state = Dispute.State.ESCALATED_OUTSIDE
    d.save(update_fields=["state"])
    return d


# --- deciding ------------------------------------------------------------------------------

def deciders(d: Dispute):
    """Officials who may decide: not the member who raised it, nobody involved."""
    return _officials(d.chama).exclude(pk=d.raised_by_id).exclude(pk__in=d.involved)


def _responses_in(d: Dispute) -> bool:
    responded = set(str(i) for i in d.messages.filter(kind=DisputeMessage.Kind.RESPONSE)
                    .values_list("membership_id", flat=True))
    return timezone.now() >= d.respond_until or set(d.involved) <= responded


def propose_outcome(d: Dispute, by: Membership, *, outcome: str, text: str,
                    member_id=None, amount_cents: int = 0) -> Dispute:
    _require_open(d)
    if d.state == Dispute.State.DECIDING:
        raise Problem("outcome_pending", "An outcome is already being decided.",
                      status.HTTP_409_CONFLICT)
    if not deciders(d).filter(pk=by.pk).exists():
        raise Problem("not_allowed", "An official who is not involved proposes the outcome.",
                      status.HTTP_403_FORBIDDEN)
    if not _responses_in(d):
        raise Problem("awaiting_response", "The people involved still have time to respond.",
                      status.HTTP_409_CONFLICT)
    text = (text or "").strip()
    if outcome not in Dispute.Outcome.values or not text:
        raise Problem("invalid", "Choose the outcome and explain it.")
    member = None
    if outcome in (Dispute.Outcome.CORRECTION, Dispute.Outcome.REFUND):
        member = Membership.objects.filter(chama=d.chama, pk=member_id).first()
        if member is None or not amount_cents:
            raise Problem("invalid", "Name the member and the amount.")
        if outcome == Dispute.Outcome.REFUND and amount_cents < 0:
            raise Problem("invalid", "A refund is a positive amount.")
    if outcome == Dispute.Outcome.REVERSAL:
        target = (LedgerEntry.objects.filter(chama=d.chama, pk=d.subject_id).first()
                  if d.subject_type == Dispute.Subject.ENTRY else None)
        if target is None or target.type == "reversal" or target.reversed_by.exists():
            raise Problem("not_reversible", "Only an entry not yet reversed can be reversed.",
                          status.HTTP_409_CONFLICT)
    s = _rules(d.chama)
    against = _loses_money(d, outcome, member, amount_cents)
    if (against is None and s["decided_by"] == "officials"
            and deciders(d).count() < s["officials"]):
        raise Problem("not_enough_deciders", "Not enough officials are free of this dispute "
                                             "to decide it; the rules need a members' vote "
                                             "instead.", status.HTTP_409_CONFLICT)
    with transaction.atomic():
        d = Dispute.objects.select_for_update().get(pk=d.pk)
        d.state, d.round = Dispute.State.DECIDING, d.round + 1
        d.outcome, d.outcome_text = outcome, text[:4000]
        d.outcome_member, d.outcome_cents, d.proposed_by = member, amount_cents, by
        d.vote_id = None
        if s["decided_by"] == "vote" or against is not None:
            from apps.votes import services as votes

            rules = version_at(d.chama, timezone.localdate()).rules
            if against is not None:
                # Officials alone never take a member's money. The members decide, without
                # the member it is about or anyone in the dispute, and two-thirds must
                # agree (protection plan 2026-10-20).
                question = (f"Take {_kes(abs(_money_lost(d, outcome, amount_cents)), d)} from "
                            f"{against.member_code}'s savings to settle a dispute: "
                            f"{text[:150]}?")
                v = votes.open_vote(
                    d.chama, subject=f"disputes.dispute:{d.id}:{d.round}", question=question,
                    rules=rules, closes_at=timezone.now() + timedelta(days=7),
                    exclude=[against.id, d.raised_by_id, *d.involved],
                    quorum_pct=max(rules["votes"]["quorum_pct"], 50),
                    pass_pct=max(rules["votes"]["pass_pct"], 66))
            else:
                v = votes.open_vote(d.chama, subject=f"disputes.dispute:{d.id}:{d.round}",
                                    question=f"Settle the dispute: {text[:200]}?",
                                    rules=rules, closes_at=timezone.now() + timedelta(days=7))
            d.vote_id = v.id
        d.save()
        DisputeMessage.objects.create(dispute=d, membership=by,
                                      kind=DisputeMessage.Kind.AGREE, round=d.round,
                                      text=f"Proposed: {outcome}. {text}"[:4000])
    return d


def _money_lost(d: Dispute, outcome: str, amount_cents: int) -> int:
    if outcome == Dispute.Outcome.CORRECTION:
        return amount_cents
    if outcome == Dispute.Outcome.REVERSAL:
        target = LedgerEntry.objects.filter(chama=d.chama, pk=d.subject_id).first()
        return -target.amount_cents if target is not None else 0
    return 0


def _loses_money(d: Dispute, outcome: str, member, amount_cents: int):
    """The member an outcome takes money from (a negative correction, or reversing money
    credited to them), or None."""
    if outcome == Dispute.Outcome.CORRECTION and amount_cents < 0:
        return member
    if outcome == Dispute.Outcome.REVERSAL:
        target = LedgerEntry.objects.filter(chama=d.chama, pk=d.subject_id).first()
        if target is not None and target.member_id and target.amount_cents > 0:
            return target.member
    return None


def _hash(d: Dispute) -> str:
    return hashlib.sha256(canonical_bytes({
        "dispute": str(d.id), "round": d.round, "outcome": d.outcome,
        "member": str(d.outcome_member_id) if d.outcome_member_id else None,
        "amount_cents": d.outcome_cents, "text": d.outcome_text})).hexdigest()


def _summary(d: Dispute, language: str) -> str:
    amount = f" {_kes(d.outcome_cents, d)}" if d.outcome_cents else ""
    if language == "sw":
        return f"KUKUBALI uamuzi wa mgogoro: {d.outcome}{amount}"
    return f"AGREE the dispute outcome: {d.outcome}{amount}"


def _require_decider(d: Dispute, by: Membership) -> None:
    if (d.state != Dispute.State.DECIDING or d.vote_id is not None
            or _rules(d.chama)["decided_by"] != "officials"):
        raise Problem("not_deciding", "No outcome is waiting for officials.",
                      status.HTTP_409_CONFLICT)
    if not deciders(d).filter(pk=by.pk).exists():
        raise Problem("not_allowed", "An official who is not involved decides.",
                      status.HTTP_403_FORBIDDEN)
    if d.messages.filter(round=d.round, membership=by,
                         kind__in=[DisputeMessage.Kind.AGREE,
                                   DisputeMessage.Kind.DISAGREE]).exists():
        raise Problem("already_answered", "You have already answered.",
                      status.HTTP_409_CONFLICT)


def start_agree(d: Dispute, by: Membership) -> SigningChallenge:
    _require_decider(d, by)
    return signing.issue(user=by.user, purpose=SigningChallenge.Purpose.DECIDE_DISPUTE,
                         subject=f"disputes.dispute:{d.id}", content_hash=_hash(d),
                         action_text=_summary(d, by.user.language))


def agree(d: Dispute, by: Membership, *, challenge_id, sms_code: str) -> Dispute:
    _require_decider(d, by)
    signing.confirm(user=by.user, challenge_id=challenge_id, code=sms_code,
                    purpose=SigningChallenge.Purpose.DECIDE_DISPUTE,
                    subject=f"disputes.dispute:{d.id}", content_hash=_hash(d))
    with transaction.atomic():
        d = Dispute.objects.select_for_update().get(pk=d.pk)
        _require_decider(d, by)
        DisputeMessage.objects.create(dispute=d, membership=by,
                                      kind=DisputeMessage.Kind.AGREE, round=d.round,
                                      challenge_id=challenge_id)
        agrees = d.messages.filter(round=d.round, kind=DisputeMessage.Kind.AGREE).count()
        if agrees >= _rules(d.chama)["officials"]:
            _apply(d)
    d.refresh_from_db()
    return d


def disagree(d: Dispute, by: Membership, *, text: str) -> Dispute:
    _require_decider(d, by)
    with transaction.atomic():
        d = Dispute.objects.select_for_update().get(pk=d.pk)
        DisputeMessage.objects.create(dispute=d, membership=by,
                                      kind=DisputeMessage.Kind.DISAGREE, round=d.round,
                                      text=(text or "").strip()[:4000])
        disagrees = d.messages.filter(round=d.round,
                                      kind=DisputeMessage.Kind.DISAGREE).count()
        if deciders(d).count() - disagrees < _rules(d.chama)["officials"]:
            _back_to_open(d)
    return d


def _back_to_open(d: Dispute) -> None:
    """The proposed outcome cannot pass; officials may propose another."""
    d.state, d.outcome, d.outcome_text = Dispute.State.AWAITING_RESPONSE, "", ""
    d.outcome_member, d.outcome_cents, d.vote_id = None, 0, None
    d.save(update_fields=["state", "outcome", "outcome_text", "outcome_member",
                          "outcome_cents", "vote_id"])


def on_vote_closed(vote) -> None:
    _, dispute_id, round_ = vote.subject.split(":")
    d = Dispute.objects.select_for_update().get(pk=dispute_id)
    if d.state != Dispute.State.DECIDING or str(d.round) != round_:
        return
    if vote.state == "passed":
        _apply(d)
    else:
        _back_to_open(d)


def _apply(d: Dispute) -> None:
    """Write the outcome as entries. Called with the dispute locked."""
    rules_version = version_at(d.chama, timezone.localdate())
    base = {"dispute_id": str(d.id), "outcome": d.outcome, "text": d.outcome_text,
            "raised_by": (d.raised_by.member_code if d.raised_by
                          else "automatic"), "subject": d.subject_type,
            "subject_id": str(d.subject_id) if d.subject_id else None}
    if d.outcome == Dispute.Outcome.REVERSAL:
        target = LedgerEntry.objects.get(chama=d.chama, pk=d.subject_id)
        ledger.append(chama=d.chama, type="reversal", source="dispute", source_id=d.id,
                      member=target.member, amount_cents=-target.amount_cents,
                      period=target.period, reverses=target,
                      rule_version_id=rules_version.id, rule_refs=[REF_DISPUTES],
                      decision={**base, "reason": "decided in a dispute"})
    elif d.outcome == Dispute.Outcome.CORRECTION:
        ledger.append(chama=d.chama, type="correction", source="dispute", source_id=d.id,
                      member=d.outcome_member, amount_cents=d.outcome_cents,
                      rule_version_id=rules_version.id, rule_refs=[REF_DISPUTES],
                      decision=base)
    entry = ledger.append(
        chama=d.chama, type="dispute_resolved", source="dispute", source_id=d.id,
        member=d.outcome_member, amount_cents=0, rule_version_id=rules_version.id,
        rule_refs=[REF_DISPUTES],
        decision={**base, "amount_cents": d.outcome_cents,
                  "decided_by": "vote" if d.vote_id else "officials",
                  "agreed": [m.membership.member_code for m in d.messages.filter(
                      round=d.round, kind=DisputeMessage.Kind.AGREE)
                      .select_related("membership")]})
    d.state, d.entry = Dispute.State.RESOLVED, entry
    d.save(update_fields=["state", "entry"])


# --- the evidence pack ---------------------------------------------------------------------

def _entry(e: LedgerEntry) -> dict:
    return {"seq": e.seq, "type": e.type, "amount_cents": e.amount_cents, "period": e.period,
            "member_code": e.member_code, "rule_refs": e.rule_refs, "decision": e.decision,
            "at": e.at.isoformat(), "receipt": e.receipt_code, "hash": e.hash,
            "prev_hash": e.prev_hash, "signatures_digest": e.signatures_digest}


def evidence(d: Dispute) -> dict:
    """Everything the records hold about the subject, attached automatically (5.14 step
    3), for the chama, or for mediation, a Small Claims Court or the police."""
    from apps.feeds.models import BankTransaction
    from apps.fines.models import Fine
    from apps.meetings.models import Minutes
    from apps.payouts.models import Proposal

    pack: dict = {"dispute": str(d.id), "chama": d.chama.name, "subject": d.subject_type,
                  "entries": []}
    sid = d.subject_id
    if d.subject_type == Dispute.Subject.ENTRY:
        e = LedgerEntry.objects.get(pk=sid)
        pack["entries"] = [_entry(x) for x in [e, *e.reversed_by.all()]]
    elif d.subject_type == Dispute.Subject.FINE:
        f = Fine.objects.get(pk=sid)
        pack["fine"] = {"rule_ref": f.rule_ref, "period": f.period,
                        "amount_cents": f.amount_cents, "state": f.state}
        pack["entries"] = [_entry(x) for x in LedgerEntry.objects.filter(
            chama=d.chama, decision__fine_id=str(f.id)).order_by("seq")]
        pack["entries"].insert(0, _entry(f.raised_entry))
    elif d.subject_type == Dispute.Subject.PROPOSAL:
        p = Proposal.objects.get(pk=sid)
        pack["proposal"] = {"kind": p.kind, "amount_cents": p.amount_cents,
                            "payee": p.payee_name, "purpose": p.purpose, "state": p.state,
                            "approvals": [{"member_code": a.membership.member_code,
                                           "decision": a.decision, "at": a.at.isoformat(),
                                           "method": "device" if a.device_id else "sms"}
                                          for a in p.approvals.select_related("membership")]}
        if p.entry_id:
            pack["entries"] = [_entry(p.entry)]
    elif d.subject_type == Dispute.Subject.PAYMENT:
        t = BankTransaction.objects.get(pk=sid)
        pack["bank_line"] = {"value_date": t.value_date.isoformat(), "direction": t.direction,
                             "amount_cents": t.amount_cents, "reference": t.external_ref,
                             "narration": t.narration, "state": t.state,
                             "statement": str(t.statement_id) if t.statement_id else None}
        pack["entries"] = [_entry(x) for x in LedgerEntry.objects.filter(
            chama=d.chama, source_id=t.id).order_by("seq")]
    elif d.subject_type == Dispute.Subject.MINUTES:
        mn = Minutes.objects.get(pk=sid)
        pack["minutes"] = {"version": mn.version, "hash": mn.hash, "text": mn.text,
                           "objections": [o.reason for o in mn.objections.all()]}
    pack["messages"] = [{"by": m.membership.member_code, "kind": m.kind, "text": m.text,
                         "at": m.at.isoformat()}
                        for m in d.messages.select_related("membership").order_by("at")]
    return pack
