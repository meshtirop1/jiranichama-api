"""Money out (5.6, 5.7). See docs/flows/money-out.md.

An official proposes; the rules decide how many officials must approve; each approves
with an SMS code (the payee never counts); a signatory pays at the bank and taps Paid;
the bank debit is matched back. Any other debit that is not a bank charge is an
unapproved debit, and every member is told the same day.

Phone-chip signatures (6.7) replace the SMS code for approvals in a later slice; the
approval records and checks here stay the same.
"""

import hashlib
import re
from datetime import timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status

from apps.chamas.models import OFFICIAL_ROLES, Chama, Membership
from apps.contributions.projections import position
from apps.core.canonical import canonical_bytes
from apps.core.problems import Problem
from apps.devices import signing
from apps.devices.models import SigningChallenge
from apps.feeds.models import BankTransaction, Statement
from apps.ledger import services as ledger
from apps.notify.sms import SmsFailed, send_sms
from apps.rules.models import version_at

from .models import Approval, MoneyOutConsent, Proposal, UnapprovedDebit

CONSENTS_NEEDED = 2
PROMISED = (Proposal.State.APPROVED, Proposal.State.PAID_PENDING_MATCH)
OPEN = (Proposal.State.COLLECTING,) + PROMISED

# Bank charges are recorded, never alerted. Matched on the narration (5.7 step 2).
_CHARGE = re.compile(r"\b(charges?|fees?|commission|excise|levy|ledger fee|sms alert|"
                     r"maintenance|stamp duty|cot)\b", re.I)


def _kes(cents: int) -> str:
    return f"KES {cents / 100:,.0f}" if cents % 100 == 0 else f"KES {cents / 100:,.2f}"


def _official(by: Membership) -> None:
    if by.role not in OFFICIAL_ROLES or not by.is_current:
        raise Problem("not_allowed", "Only an official does this.", status.HTTP_403_FORBIDDEN)


def _money_out(chama: Chama) -> None:
    if chama.state != Chama.State.ACTIVE:
        raise Problem("chama_not_active", "This is done once the chama is active.",
                      status.HTTP_409_CONFLICT)
    if chama.mode != Chama.Mode.MONEY_OUT:
        raise Problem("money_out_off", "Money out is not switched on for this chama.",
                      status.HTTP_409_CONFLICT)


# --- Switching money-out on ---------------------------------------------------------------

def _consent_hash(chama: Chama) -> str:
    return hashlib.sha256(canonical_bytes({"chama": str(chama.id), "mode": "money_out"})
                          ).hexdigest()


def start_enable(chama: Chama, by: Membership) -> SigningChallenge:
    _official(by)
    if chama.mode == Chama.Mode.MONEY_OUT:
        raise Problem("money_out_on", "Money out is already on.", status.HTTP_409_CONFLICT)
    action = ("SWITCH ON money out: payouts approved in JiraniChama" if by.user.language != "sw"
              else "KUWASHA malipo: malipo yanayoidhinishwa ndani ya JiraniChama")
    return signing.issue(user=by.user, purpose=SigningChallenge.Purpose.ENABLE_MONEY_OUT,
                         subject=f"chamas.chama:{chama.id}", content_hash=_consent_hash(chama),
                         action_text=action)


def enable(chama: Chama, by: Membership, *, challenge_id, sms_code: str) -> int:
    """Records this official's consent; with two, the chama is in money-out mode. Returns
    how many officials have agreed."""
    _official(by)
    signing.confirm(user=by.user, challenge_id=challenge_id, code=sms_code,
                    purpose=SigningChallenge.Purpose.ENABLE_MONEY_OUT,
                    subject=f"chamas.chama:{chama.id}", content_hash=_consent_hash(chama))
    with transaction.atomic():
        chama = Chama.objects.select_for_update().get(pk=chama.pk)
        MoneyOutConsent.objects.get_or_create(chama=chama, membership=by)
        n = MoneyOutConsent.objects.filter(chama=chama).count()
        if n >= CONSENTS_NEEDED and chama.mode != Chama.Mode.MONEY_OUT:
            chama.mode = Chama.Mode.MONEY_OUT
            chama.save(update_fields=["mode"])
    return n


# --- Proposing ------------------------------------------------------------------------------

def available_cents(chama: Chama) -> int | None:
    """The last reconciled bank balance, less money already promised. None when no bank
    statement has been accepted yet."""
    last = (Statement.objects.filter(chama=chama, state=Statement.State.ACCEPTED)
            .order_by("-period_to", "-created_at").first())
    if last is None:
        return None
    promised = (Proposal.objects.filter(chama=chama, state__in=PROMISED)
                .aggregate(t=Sum("amount_cents"))["t"] or 0)
    return last.closing_cents - promised


def _refuse(code: str, rule: str, message: str) -> dict:
    return {"code": code, "rule": rule, "message": message}


def _rules_check(chama: Chama, *, amount_cents: int, payee: Membership | None, today
                 ) -> tuple[dict, dict | None, object]:
    """Which approvals the amount needs, or why the rules refuse it (5.6 step 2)."""
    version = version_at(chama, today)
    if version is None:
        raise Problem("no_rules", "The chama has no rules in force.", status.HTTP_409_CONFLICT)
    tiers = version.rules["approvals"]["tiers"]
    i, tier = next((i, t) for i, t in enumerate(tiers, start=1)
                   if t["up_to_cents"] is None or amount_cents <= t["up_to_cents"])
    if payee is not None:
        behind = position(payee, today).arrears_cents
        if behind > 0:
            return tier, _refuse("payee_in_arrears", "3.1",
                                 f"{payee.member_code} has {_kes(behind)} of contributions "
                                 "unpaid."), version
    available = available_cents(chama)
    if available is None:
        return tier, _refuse("no_reconciled_balance", "0.1",
                             "No bank statement has been accepted yet, so the balance is "
                             "not known."), version
    if amount_cents > available:
        return tier, _refuse("not_enough_money", "0.1",
                             f"Only {_kes(max(available, 0))} is available after money "
                             "already promised."), version
    if tier["member_vote"]:
        # Member votes arrive with meetings and votes (5.11); until then, amounts that
        # need one are refused rather than approved without it.
        return tier, _refuse("member_vote_needed", f"4.{i}",
                             f"This amount needs a vote of the members (rule 4.{i}). Votes "
                             "in the app are not available yet."), version
    return {**tier, "rule": f"4.{i}"}, None, version


def propose(chama: Chama, by: Membership, *, kind: str, amount_cents: int, purpose: str,
            payee: Membership | None = None, payee_name: str = "",
            payee_account: str = "") -> Proposal:
    _money_out(chama)
    _official(by)
    if amount_cents <= 0:
        raise Problem("invalid_amount", "Enter the amount.")
    purpose = (purpose or "").strip()[:200]
    if not purpose:
        raise Problem("purpose_required", "Say what the money is for.")
    if kind == Proposal.Kind.PAYOUT:
        if payee is None or payee.chama_id != chama.id or not payee.is_current:
            raise Problem("invalid_member", "Choose a member of this chama.")
        payee_name = payee.user.display_name or payee.member_code
    else:
        payee, payee_name = None, (payee_name or "").strip()[:120]
        if not payee_name:
            raise Problem("payee_required", "Who is being paid?")
    today = timezone.localdate()
    tier, refusal, version = _rules_check(chama, amount_cents=amount_cents, payee=payee,
                                          today=today)
    now = timezone.now()
    return Proposal.objects.create(
        chama=chama, kind=kind, amount_cents=amount_cents, payee_membership=payee,
        payee_name=payee_name, payee_account=(payee_account or "").strip()[:120],
        purpose=purpose, proposed_by=by, rule_version_id=version.id,
        needs_officials=tier["officials"], needs_vote=tier["member_vote"],
        refusal=refusal or {},
        deadline_at=None if refusal else
        now + timedelta(hours=version.rules["approvals"]["deadline_hours"]),
        state=Proposal.State.REFUSED if refusal else Proposal.State.COLLECTING,
        state_reason=refusal["message"] if refusal else "")


# --- Approving ------------------------------------------------------------------------------

def expire_due(chama: Chama | None = None) -> int:
    """Proposals whose approvals did not come in by the deadline. Runs hourly and on
    every read, so a stale proposal is never shown as still open."""
    qs = Proposal.objects.filter(state=Proposal.State.COLLECTING, deadline_at__lte=timezone.now())
    if chama is not None:
        qs = qs.filter(chama=chama)
    n = 0
    for p in qs:
        p.state, p.state_reason = Proposal.State.EXPIRED, "Approvals were not complete in time."
        p.save(update_fields=["state", "state_reason"])
        n += 1
    return n


def eligible_approvers(p: Proposal):
    """Officials who may approve: never the person being paid (v1.1, 6.8)."""
    qs = Membership.objects.filter(chama=p.chama, role__in=OFFICIAL_ROLES,
                                   state=Membership.State.ACTIVE)
    if p.payee_membership_id:
        qs = qs.exclude(pk=p.payee_membership_id)
    return qs


def _content_hash(p: Proposal) -> str:
    return hashlib.sha256(canonical_bytes({
        "proposal": str(p.id), "kind": p.kind, "amount_cents": p.amount_cents,
        "payee": p.payee_name, "payee_account": p.payee_account, "purpose": p.purpose,
    })).hexdigest()


def _require_can_answer(p: Proposal, by: Membership) -> None:
    expire_due(p.chama)
    p.refresh_from_db()
    if p.state != Proposal.State.COLLECTING:
        raise Problem("not_collecting", "This proposal is not waiting for approvals.",
                      status.HTTP_409_CONFLICT)
    if p.payee_membership_id == by.id:
        raise Problem("payee_cannot_approve", "Nobody approves a payment to themselves.",
                      status.HTTP_403_FORBIDDEN)
    if not eligible_approvers(p).filter(pk=by.pk).exists():
        raise Problem("not_allowed", "Only an official approves money out.",
                      status.HTTP_403_FORBIDDEN)
    if Approval.objects.filter(proposal=p, membership=by).exists():
        raise Problem("already_answered", "You have already answered this proposal.",
                      status.HTTP_409_CONFLICT)


def summary(p: Proposal, language: str) -> str:
    amount = _kes(p.amount_cents)
    to = p.payee_name + (f", {p.payee_account}" if p.payee_account else "")
    if language == "sw":
        return f"KUIDHINISHA kulipa {to} {amount} kwa {p.purpose}"
    return f"APPROVE paying {to} {amount} for {p.purpose}"


def start_approve(p: Proposal, by: Membership) -> SigningChallenge:
    _require_can_answer(p, by)
    return signing.issue(user=by.user, purpose=SigningChallenge.Purpose.APPROVE_PROPOSAL,
                         subject=f"payouts.proposal:{p.id}", content_hash=_content_hash(p),
                         action_text=summary(p, by.user.language))


def approve(p: Proposal, by: Membership, *, challenge_id, sms_code: str) -> Proposal:
    _require_can_answer(p, by)
    signing.confirm(user=by.user, challenge_id=challenge_id, code=sms_code,
                    purpose=SigningChallenge.Purpose.APPROVE_PROPOSAL,
                    subject=f"payouts.proposal:{p.id}", content_hash=_content_hash(p))
    with transaction.atomic():
        p = Proposal.objects.select_for_update().get(pk=p.pk)
        _require_can_answer(p, by)
        Approval.objects.create(proposal=p, membership=by, role=by.role,
                                decision=Approval.Decision.APPROVE, challenge_id=challenge_id)
        approvals = p.approvals.filter(decision=Approval.Decision.APPROVE).count()
        if approvals >= p.needs_officials:
            p.state = Proposal.State.APPROVED
            p.save(update_fields=["state"])
    return p


def decline(p: Proposal, by: Membership, reason: str) -> Proposal:
    reason = (reason or "").strip()[:300]
    if not reason:
        raise Problem("reason_required", "Say why you decline.")
    with transaction.atomic():
        p = Proposal.objects.select_for_update().get(pk=p.pk)
        _require_can_answer(p, by)
        Approval.objects.create(proposal=p, membership=by, role=by.role,
                                decision=Approval.Decision.DECLINE, reason=reason)
        answered_no = set(p.approvals.filter(decision=Approval.Decision.DECLINE)
                          .values_list("membership_id", flat=True))
        could_still = eligible_approvers(p).exclude(pk__in=answered_no).count()
        if could_still < p.needs_officials:
            p.state = Proposal.State.REJECTED
            p.state_reason = "Too many officials declined."
            p.save(update_fields=["state", "state_reason"])
    return p


def cancel(p: Proposal, by: Membership, reason: str) -> Proposal:
    with transaction.atomic():
        p = Proposal.objects.select_for_update().get(pk=p.pk)
        if p.proposed_by_id != by.id:
            raise Problem("not_allowed", "Only the official who proposed it cancels it.",
                          status.HTTP_403_FORBIDDEN)
        if p.state not in (Proposal.State.COLLECTING, Proposal.State.APPROVED):
            raise Problem("cannot_cancel", "It can no longer be cancelled.",
                          status.HTTP_409_CONFLICT)
        p.state = Proposal.State.CANCELLED
        p.state_reason = (reason or "").strip()[:300] or "Cancelled by the proposer."
        p.save(update_fields=["state", "state_reason"])
    return p


# --- Paid at the bank, and matching the debit ------------------------------------------------

def mark_paid(p: Proposal, by: Membership, bank_reference: str) -> Proposal:
    """A signatory paid at the bank (5.6 step 7). The debit is matched when it appears."""
    _official(by)
    with transaction.atomic():
        p = Proposal.objects.select_for_update().get(pk=p.pk)
        if p.state != Proposal.State.APPROVED:
            raise Problem("not_approved", "Only an approved proposal can be marked paid.",
                          status.HTTP_409_CONFLICT)
        p.state = Proposal.State.PAID_PENDING_MATCH
        p.bank_reference = (bank_reference or "").strip()[:100]
        p.paid_by, p.paid_at = by, timezone.now()
        p.save(update_fields=["state", "bank_reference", "paid_by", "paid_at"])
    # The debit may already be in an accepted statement, waiting as unapproved.
    for ud in UnapprovedDebit.objects.filter(chama=p.chama, state=UnapprovedDebit.State.OPEN,
                                             bank_txn__amount_cents=p.amount_cents):
        if _reference_fits(p, ud.bank_txn):
            link(ud, p, by)
            break
    p.refresh_from_db()
    return p


def _reference_fits(p: Proposal, txn: BankTransaction) -> bool:
    ref = p.bank_reference.lower()
    if not ref:
        return True
    return ref in txn.external_ref.lower() or ref in txn.narration.lower()


def _write_payout(p: Proposal, txn: BankTransaction, extra: dict | None = None):
    approvers = [{"member_code": a.membership.member_code, "role": a.role,
                  "at": a.at.isoformat()}
                 for a in p.approvals.filter(decision=Approval.Decision.APPROVE)
                 .select_related("membership").order_by("at")]
    entry = ledger.append(
        chama=p.chama, type="payout", source="proposal", source_id=p.id,
        amount_cents=-p.amount_cents, member=p.payee_membership,
        rule_version_id=p.rule_version_id,
        decision={"kind": p.kind, "payee": p.payee_name, "purpose": p.purpose,
                  "approved_by": approvers, "bank_ref": txn.external_ref,
                  "value_date": txn.value_date.isoformat(), **(extra or {})})
    p.state, p.matched_txn, p.entry = Proposal.State.PAID, txn, entry
    p.save(update_fields=["state", "matched_txn", "entry"])
    txn.state = BankTransaction.State.RECORDED
    txn.save(update_fields=["state"])
    return entry


def process_debit(txn: BankTransaction) -> str:
    """A debit from an accepted statement (5.7). Returns what it became."""
    chama = txn.chama
    with transaction.atomic():
        txn = BankTransaction.objects.select_for_update().get(pk=txn.pk)
        if txn.state != BankTransaction.State.DEBIT_PENDING:
            return txn.state
        if _CHARGE.search(txn.narration or ""):
            ledger.append(chama=chama, type="bank_charge", source="bank_txn", source_id=txn.id,
                          amount_cents=-txn.amount_cents,
                          decision={"bank_ref": txn.external_ref,
                                    "value_date": txn.value_date.isoformat()})
            txn.state = BankTransaction.State.RECORDED
            txn.save(update_fields=["state"])
            return "bank_charge"
        if chama.mode != Chama.Mode.MONEY_OUT:
            return txn.state  # records-only: debits wait, as before
        candidates = [p for p in Proposal.objects.select_for_update().filter(
            chama=chama, state__in=PROMISED, amount_cents=txn.amount_cents,
            matched_txn__isnull=True).order_by("created_at") if _reference_fits(p, txn)]
        paid_first = [p for p in candidates if p.state == Proposal.State.PAID_PENDING_MATCH]
        match = (paid_first or candidates or [None])[0]
        if match is not None:
            _write_payout(match, txn)
            return "matched"
        entry = ledger.append(
            chama=chama, type="unapproved_debit_detected", source="bank_txn", source_id=txn.id,
            amount_cents=-txn.amount_cents,
            decision={"bank_ref": txn.external_ref, "value_date": txn.value_date.isoformat(),
                      "reason": "no approved proposal for this debit"})
        txn.state = BankTransaction.State.UNAPPROVED
        txn.save(update_fields=["state"])
        ud = UnapprovedDebit.objects.create(chama=chama, bank_txn=txn, entry=entry)
    transaction.on_commit(lambda: alert_members(ud.pk))
    return "unapproved"


def alert_members(ud_id) -> int:
    """Every member hears the same day (5.7 step 3b)."""
    ud = UnapprovedDebit.objects.select_related("chama", "bank_txn").get(pk=ud_id)
    amount, day = _kes(ud.bank_txn.amount_cents), ud.bank_txn.value_date.strftime("%d %b")
    sent = 0
    for m in (Membership.objects.filter(chama=ud.chama, state=Membership.State.ACTIVE)
              .select_related("user")):
        text = (f"TAHADHARI: {amount} zimetoka kwenye akaunti ya benki ya {ud.chama.name} "
                f"tarehe {day} bila idhini ndani ya JiraniChama. Viongozi wameombwa kueleza."
                if m.user.language == "sw" else
                f"ALERT: {amount} left {ud.chama.name}'s bank account on {day} with no "
                "approval in JiraniChama. Officials have been asked to explain.")
        try:
            send_sms(m.user.phone, text)
            sent += 1
        except SmsFailed:
            continue
    UnapprovedDebit.objects.filter(pk=ud.pk).update(alerted=sent)
    return sent


def link(ud: UnapprovedDebit, p: Proposal, by: Membership) -> UnapprovedDebit:
    """The debit was an approved proposal after all (paid before anyone tapped Paid)."""
    _official(by)
    with transaction.atomic():
        ud = UnapprovedDebit.objects.select_for_update().select_related("bank_txn").get(pk=ud.pk)
        p = Proposal.objects.select_for_update().get(pk=p.pk)
        if ud.state != UnapprovedDebit.State.OPEN:
            raise Problem("not_open", "This debit has already been dealt with.",
                          status.HTTP_409_CONFLICT)
        if p.chama_id != ud.chama_id or p.state not in PROMISED or p.matched_txn_id:
            raise Problem("not_linkable", "Only an approved, unpaid proposal can be linked.",
                          status.HTTP_409_CONFLICT)
        if p.amount_cents != ud.bank_txn.amount_cents:
            raise Problem("amounts_differ", "The proposal and the debit are different amounts.",
                          status.HTTP_409_CONFLICT)
        _write_payout(p, ud.bank_txn, {"resolves_unapproved": str(ud.entry_id)})
        ud.state, ud.linked_proposal = UnapprovedDebit.State.LINKED, p
        ud.resolved_by, ud.resolved_at = by, timezone.now()
        ud.save(update_fields=["state", "linked_proposal", "resolved_by", "resolved_at"])
    return ud


def explain(ud: UnapprovedDebit, by: Membership, text: str) -> UnapprovedDebit:
    _official(by)
    text = (text or "").strip()[:500]
    if not text:
        raise Problem("explanation_required", "Explain where the money went.")
    with transaction.atomic():
        ud = UnapprovedDebit.objects.select_for_update().get(pk=ud.pk)
        if ud.state != UnapprovedDebit.State.OPEN:
            raise Problem("not_open", "This debit has already been dealt with.",
                          status.HTTP_409_CONFLICT)
        ud.state, ud.explanation = UnapprovedDebit.State.EXPLAINED, text
        ud.resolved_by, ud.resolved_at = by, timezone.now()
        ud.save(update_fields=["state", "explanation", "resolved_by", "resolved_at"])
    return ud
