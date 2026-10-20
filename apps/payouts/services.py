"""Money out (5.6, 5.7). See docs/flows/money-out.md.

An official proposes; the rules decide how many officials must approve; each approves
with an SMS code (the payee never counts); a signatory pays at the bank and taps Paid;
the bank debit is matched back. Any other debit that is not a bank charge is an
unapproved debit, and every member is told the same day.

Officials with a registered phone key approve with their fingerprint, face or phone
PIN (6.7): the phone signs the exact payload, with a single-use server nonce, and the SMS
code is refused for them. Officials without the app keep the SMS code.
"""

import base64
import hashlib
import re
from datetime import timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status

from apps.chamas.models import OFFICIAL_ROLES, Chama, Membership
from apps.contributions.projections import position
from apps.core import money
from apps.core.canonical import canonical_bytes
from apps.core.problems import Problem
from apps.devices import attestation, keys, signing
from apps.devices.models import Device, DeviceNonce, SigningChallenge
from apps.feeds.models import BankTransaction, Statement
from apps.ledger import services as ledger
from apps.notify.sms import SmsFailed, send_sms
from apps.rules.models import version_at

from .models import Approval, MoneyOutConsent, Proposal, UnapprovedDebit

CONSENTS_NEEDED = 2
PROMISED = (Proposal.State.APPROVED, Proposal.State.PAID_PENDING_MATCH)
OPEN = (Proposal.State.COLLECTING,) + PROMISED

# Bank charges are recorded without an alert (5.7 step 2), but only when the narration
# reads like the bank's own charge AND the amount is small. A word like "fees" alone is
# not enough: "SCHOOL FEES" is a withdrawal like any other (protection plan 2026-10-20).
_CHARGE = re.compile(r"\b(bank charges?|service charges?|ledger fees?|sms alerts? (fee|charge)|"
                     r"maintenance fees?|excise duty|stamp duty|commission|cot|levy|"
                     r"(transaction|withdrawal|transfer|statement|cheque) (fee|charge)s?|"
                     r"charges? on|fee on)\b", re.I)
# The most one line can be and still count as a bank charge: about KES 1,000.
BANK_CHARGE_MAX_KES_CENTS = 100_000


def _is_bank_charge(txn) -> bool:
    from apps.core import countries

    limit = BANK_CHARGE_MAX_KES_CENTS * countries.KES_RATE.get(txn.chama.currency, 1)
    return bool(_CHARGE.search(txn.narration or "")) and txn.amount_cents <= limit


def _kes(cents: int, of=None) -> str:
    return money.fmt(cents, of)


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


def _rules_check(chama: Chama, *, amount_cents: int, payee: Membership | None, today,
                 allow_arrears: bool = False) -> tuple[dict, dict | None, object]:
    """Which approvals the amount needs, or why the rules refuse it (5.6 step 2)."""
    version = version_at(chama, today)
    if version is None:
        raise Problem("no_rules", "The chama has no rules in force.", status.HTTP_409_CONFLICT)
    tiers = version.rules["approvals"]["tiers"]
    i, tier = next((i, t) for i, t in enumerate(tiers, start=1)
                   if t["up_to_cents"] is None or amount_cents <= t["up_to_cents"])
    signers = Membership.objects.filter(chama=chama, role__in=OFFICIAL_ROLES,
                                        state=Membership.State.ACTIVE)
    if payee is not None:
        signers = signers.exclude(pk=payee.pk)
    if signers.count() < tier["officials"]:
        refusal = _refuse("election_needed", f"4.{i}",
                          f"This needs {tier['officials']} officials to approve, and only "
                          f"{signers.count()} can. The chama needs an election first.")
        return tier, refusal, version
    if payee is not None and not allow_arrears:
        behind = position(payee, today).arrears_cents
        if behind > 0:
            return tier, _refuse("payee_in_arrears", "3.1",
                                 f"{payee.member_code} has {_kes(behind, chama)} of contributions "
                                 "unpaid."), version
    available = available_cents(chama)
    if available is None:
        return tier, _refuse("no_reconciled_balance", "0.1",
                             "No bank statement has been accepted yet, so the balance is "
                             "not known."), version
    if amount_cents > available:
        return tier, _refuse("not_enough_money", "0.1",
                             f"Only {_kes(max(available, 0), chama)} is available after money "
                             "already promised."), version
    return {**tier, "rule": f"4.{i}"}, None, version


def propose(chama: Chama, by: Membership, *, kind: str, amount_cents: int, purpose: str,
            payee: Membership | None = None, payee_name: str = "",
            payee_account: str = "", allow_arrears: bool = False,
            deduct_cents: int = 0) -> Proposal:
    """``allow_arrears`` and ``deduct_cents`` are for a merry-go-round turn paid less what
    the member owes (rule 8.2 "deduct"); see rotation.propose_next."""
    _money_out(chama)
    if open_breaches(chama) and kind != Proposal.Kind.SETTLEMENT:
        raise Problem("breach_open", "Money left the bank without approval. Nothing can be "
                                     "paid until the members accept an explanation.",
                      status.HTTP_409_CONFLICT)
    # A loan is proposed by the borrower (loans), a settlement by the leaving member or the
    # official recording a death (exits); everything else by an official.
    if kind not in (Proposal.Kind.LOAN, Proposal.Kind.SETTLEMENT):
        _official(by)
    if amount_cents <= 0:
        raise Problem("invalid_amount", "Enter the amount.")
    purpose = (purpose or "").strip()[:200]
    if not purpose:
        raise Problem("purpose_required", "Say what the money is for.")
    if kind in (Proposal.Kind.PAYOUT, Proposal.Kind.LOAN):
        if payee is None or payee.chama_id != chama.id or not payee.is_current:
            raise Problem("invalid_member", "Choose a member of this chama.")
        payee_name = payee.user.display_name or payee.member_code
    elif kind == Proposal.Kind.SETTLEMENT and payee is not None:
        if payee.chama_id != chama.id:
            raise Problem("invalid_member", "Choose a member of this chama.")
        payee_name = payee.user.display_name or payee.member_code
    else:
        payee, payee_name = None, (payee_name or "").strip()[:120]
        if not payee_name:
            raise Problem("payee_required", "Who is being paid?")
    today = timezone.localdate()
    tier, refusal, version = _rules_check(chama, amount_cents=amount_cents, payee=payee,
                                          today=today, allow_arrears=allow_arrears)
    now = timezone.now()
    p = Proposal.objects.create(
        chama=chama, kind=kind, amount_cents=amount_cents, payee_membership=payee,
        payee_name=payee_name, payee_account=(payee_account or "").strip()[:120],
        purpose=purpose, proposed_by=by, rule_version_id=version.id,
        needs_officials=tier["officials"], needs_vote=tier["member_vote"],
        refusal=refusal or {}, deduct_cents=deduct_cents,
        deadline_at=None if refusal else
        now + timedelta(hours=version.rules["approvals"]["deadline_hours"]),
        state=Proposal.State.REFUSED if refusal else Proposal.State.COLLECTING,
        state_reason=refusal["message"] if refusal else "")
    if not refusal and p.needs_vote:
        # The rules want the members' say too (rule 4.x, 5.11): a vote runs alongside the
        # officials' approvals, with the same deadline. The payee does not vote.
        from apps.votes import services as votes

        to = p.payee_name + (f", {p.payee_account}" if p.payee_account else "")
        votes.open_vote(chama, subject=f"payouts.proposal:{p.id}",
                        question=f"Pay {to} {_kes(p.amount_cents, chama)} for {p.purpose}?",
                        rules=version.rules, closes_at=p.deadline_at,
                        exclude=[payee.id] if payee else None)
    return p


# --- Approving ------------------------------------------------------------------------------

def _vote_of(p: Proposal):
    from apps.votes.services import for_subject

    return for_subject(f"payouts.proposal:{p.id}") if p.needs_vote else None


def _maybe_approved(p: Proposal) -> None:
    """Approved once enough officials have approved and, where the rules want it, the
    members' vote has passed. Called with the proposal's row locked."""
    if p.state != Proposal.State.COLLECTING:
        return
    approvals = p.approvals.filter(decision=Approval.Decision.APPROVE).count()
    if approvals < p.needs_officials:
        return
    vote = _vote_of(p)
    if vote is not None and vote.state != "passed":
        return
    p.state = Proposal.State.APPROVED
    p.save(update_fields=["state"])


def on_vote_closed(vote) -> None:
    """The members' vote on a proposal has closed (votes.services._close)."""
    p = Proposal.objects.select_for_update().get(pk=vote.subject.split(":", 1)[1])
    if vote.state == "passed":
        _maybe_approved(p)
    elif p.state == Proposal.State.COLLECTING:
        p.state = Proposal.State.REJECTED
        p.state_reason = ("The members voted no." if vote.state == "failed"
                          else "Not enough members voted.")
        p.save(update_fields=["state", "state_reason"])


def expire_due(chama: Chama | None = None) -> int:
    """Proposals whose approvals did not come in by the deadline. Runs hourly and on
    every read, so a stale proposal is never shown as still open."""
    from apps.votes.services import close_due

    close_due(chama)  # a vote closing on the deadline decides first
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
    """Officials who may approve: never the person being paid (v1.1, 6.8), and never the
    person who proposed it: maker and checker are always different people."""
    qs = Membership.objects.filter(chama=p.chama, role__in=OFFICIAL_ROLES,
                                   state=Membership.State.ACTIVE)
    if p.payee_membership_id:
        qs = qs.exclude(pk=p.payee_membership_id)
    if p.proposed_by_id:
        qs = qs.exclude(pk=p.proposed_by_id)
    return qs


def _content_hash(p: Proposal) -> str:
    return hashlib.sha256(canonical_bytes({
        "proposal": str(p.id), "kind": p.kind, "amount_cents": p.amount_cents,
        "payee": p.payee_name, "payee_account": p.payee_account, "purpose": p.purpose,
    })).hexdigest()


def _require_not_disputed(p: Proposal) -> None:
    from apps.disputes.services import open_on_proposal

    if open_on_proposal(p.id):
        raise Problem("disputed", "A member has raised a dispute about this; it is paused "
                                  "until the dispute is settled.", status.HTTP_409_CONFLICT)


def _require_can_answer(p: Proposal, by: Membership) -> None:
    expire_due(p.chama)
    p.refresh_from_db()
    _require_not_disputed(p)
    if p.state != Proposal.State.COLLECTING:
        raise Problem("not_collecting", "This proposal is not waiting for approvals.",
                      status.HTTP_409_CONFLICT)
    if p.payee_membership_id == by.id:
        raise Problem("payee_cannot_approve", "Nobody approves a payment to themselves.",
                      status.HTTP_403_FORBIDDEN)
    if p.proposed_by_id == by.id:
        raise Problem("proposer_cannot_approve", "Whoever proposes a payment cannot approve "
                                                 "it: another official does.",
                      status.HTTP_403_FORBIDDEN)
    if not eligible_approvers(p).filter(pk=by.pk).exists():
        raise Problem("not_allowed", "Only an official approves money out.",
                      status.HTTP_403_FORBIDDEN)
    if Approval.objects.filter(proposal=p, membership=by).exists():
        raise Problem("already_answered", "You have already answered this proposal.",
                      status.HTTP_409_CONFLICT)


def summary(p: Proposal, language: str) -> str:
    amount = _kes(p.amount_cents, p)
    to = p.payee_name + (f", {p.payee_account}" if p.payee_account else "")
    if language == "sw":
        return f"KUIDHINISHA kulipa {to} {amount} kwa {p.purpose}"
    return f"APPROVE paying {to} {amount} for {p.purpose}"


def _sms_allowed(by: Membership) -> None:
    if keys.active_device(by.user) is not None:
        raise Problem("use_device_key", "This phone is set up for approvals: approve with "
                                        "your fingerprint in the app.",
                      status.HTTP_409_CONFLICT)


def start_approve(p: Proposal, by: Membership) -> SigningChallenge:
    _require_can_answer(p, by)
    _sms_allowed(by)
    return signing.issue(user=by.user, purpose=SigningChallenge.Purpose.APPROVE_PROPOSAL,
                         subject=f"payouts.proposal:{p.id}", content_hash=_content_hash(p),
                         action_text=summary(p, by.user.language))


def approve(p: Proposal, by: Membership, *, challenge_id, sms_code: str) -> Proposal:
    _require_can_answer(p, by)
    _sms_allowed(by)
    signing.confirm(user=by.user, challenge_id=challenge_id, code=sms_code,
                    purpose=SigningChallenge.Purpose.APPROVE_PROPOSAL,
                    subject=f"payouts.proposal:{p.id}", content_hash=_content_hash(p))
    with transaction.atomic():
        p = Proposal.objects.select_for_update().get(pk=p.pk)
        _require_can_answer(p, by)
        Approval.objects.create(proposal=p, membership=by, role=by.role,
                                decision=Approval.Decision.APPROVE, challenge_id=challenge_id)
        _maybe_approved(p)
    return p


# --- Approving with the phone's key (6.7) ------------------------------------------------

def signing_payload(p: Proposal, nonce: str) -> dict:
    """What the phone signs: everything that makes this approval this approval, and a
    single-use nonce, so the signature cannot be used for anything else."""
    return {
        "v": 1, "purpose": "approve_proposal", "chama_id": str(p.chama_id),
        "proposal_id": str(p.id), "kind": p.kind, "amount_cents": p.amount_cents,
        "deduct_cents": p.deduct_cents, "payee": p.payee_name,
        "payee_account": p.payee_account, "purpose_text": p.purpose,
        "rule_version_id": str(p.rule_version_id) if p.rule_version_id else None,
        "nonce": nonce,
    }


def start_device_approve(p: Proposal, by: Membership) -> dict:
    from apps.devices.changes import require_can_sign

    require_can_sign(by.user)
    _require_can_answer(p, by)
    device = keys.active_device(by.user)
    if device is None:
        raise Problem("no_device_key", "Set up this phone for approvals first.",
                      status.HTTP_409_CONFLICT)
    n = keys.issue_nonce(by.user, DeviceNonce.Use.SIGN, f"payouts.proposal:{p.id}")
    payload = signing_payload(p, n.nonce)
    return {"device_id": str(device.id), "payload": payload,
            "payload_hash": hashlib.sha256(canonical_bytes(payload)).hexdigest()}


def approve_with_device(p: Proposal, by: Membership, *, device_id, nonce: str,
                        payload_hash: str, signature_b64: str) -> Proposal:
    """The phone signed SHA-256 over the canonical payload, after the official's
    fingerprint, face or PIN. A payload that changed since it was shown is refused (409)."""
    device = Device.objects.filter(pk=device_id, user=by.user,
                                   state=Device.State.ACTIVE).first()
    if device is None:
        raise Problem("device_invalid", "This phone's key is not active.",
                      status.HTTP_403_FORBIDDEN)
    with transaction.atomic():
        p = Proposal.objects.select_for_update().get(pk=p.pk)
        _require_can_answer(p, by)
        keys.take_nonce(by.user, DeviceNonce.Use.SIGN, nonce, f"payouts.proposal:{p.id}")
        message = canonical_bytes(signing_payload(p, nonce))
        if hashlib.sha256(message).hexdigest() != payload_hash:
            raise Problem("payload_changed", "What you are approving has changed. Look "
                                             "again.", status.HTTP_409_CONFLICT)
        try:
            signature = base64.b64decode(signature_b64)
        except (ValueError, TypeError):
            signature = b""
        if not attestation.verify_signature(bytes(device.public_key_der), message, signature):
            raise Problem("signature_invalid", "The phone's signature did not check out.",
                          status.HTTP_400_BAD_REQUEST)
        Approval.objects.create(proposal=p, membership=by, role=by.role,
                                decision=Approval.Decision.APPROVE, device=device,
                                payload_hash=payload_hash, signature=signature)
        _maybe_approved(p)
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
        _require_not_disputed(p)
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
    approved = list(p.approvals.filter(decision=Approval.Decision.APPROVE)
                    .select_related("membership", "device").order_by("at"))
    approvers = [{"member_code": a.membership.member_code, "role": a.role,
                  "at": a.at.isoformat(), "method": "device" if a.device_id else "sms"}
                 for a in approved]
    # Phone-key signatures are bound into the entry's hash (signatures_digest), so the
    # chain itself shows which keys approved.
    signatures = [(a.membership.member_code, a.device.fingerprint,
                   base64.b64encode(bytes(a.signature)).decode())
                  for a in approved if a.device_id]
    if p.deduct_cents:
        extra = {**(extra or {}), "deducted_for_arrears": p.deduct_cents,
                 "paid_by_bank": p.amount_cents}
    if p.kind == Proposal.Kind.LOAN:
        from apps.loans.models import Loan

        loan = Loan.objects.filter(proposal=p).first()
        extra = {**(extra or {}), "loan_id": str(loan.id) if loan else None}
    entry = ledger.append(
        chama=p.chama, type="loan_disbursed" if p.kind == Proposal.Kind.LOAN else "payout",
        source="proposal", source_id=p.id,
        amount_cents=-(p.amount_cents + p.deduct_cents), member=p.payee_membership,
        rule_version_id=p.rule_version_id, signatures=signatures,
        decision={"kind": p.kind, "payee": p.payee_name, "purpose": p.purpose,
                  "approved_by": approvers, "bank_ref": txn.external_ref,
                  "value_date": txn.value_date.isoformat(), **(extra or {})})
    if p.deduct_cents:
        # The second entry: what the member owed, paid out of their turn (rule 8.2), judged
        # by the rules like any payment.
        from apps.reconciliation.services import apply_payment

        apply_payment(txn=txn, member=p.payee_membership, amount_cents=p.deduct_cents,
                      value_date=txn.value_date, version=version_at(p.chama, txn.value_date),
                      base={"method": "payout_deduction", "proposal": str(p.id),
                            "bank_ref": txn.external_ref,
                            "value_date": txn.value_date.isoformat()})
    p.state, p.matched_txn, p.entry = Proposal.State.PAID, txn, entry
    p.save(update_fields=["state", "matched_txn", "entry"])
    txn.state = BankTransaction.State.RECORDED
    txn.save(update_fields=["state"])
    from .rotation import on_paid

    on_paid(p)
    if p.kind == Proposal.Kind.LOAN:
        from apps.loans.services import on_paid as loan_paid

        loan_paid(p)
    if p.kind == Proposal.Kind.SETTLEMENT:
        from apps.exits.services import on_paid as exit_paid

        exit_paid(p)
    return entry


def process_debit(txn: BankTransaction) -> str:
    """A debit from an accepted statement (5.7). Returns what it became."""
    chama = txn.chama
    with transaction.atomic():
        txn = BankTransaction.objects.select_for_update().get(pk=txn.pk)
        if txn.state != BankTransaction.State.DEBIT_PENDING:
            return txn.state
        if _is_bank_charge(txn):
            ledger.append(chama=chama, type="bank_charge", source="bank_txn", source_id=txn.id,
                          amount_cents=-txn.amount_cents,
                          decision={"bank_ref": txn.external_ref,
                                    "value_date": txn.value_date.isoformat()})
            txn.state = BankTransaction.State.RECORDED
            txn.save(update_fields=["state"])
            return "bank_charge"
        # Records-only chamas cannot approve payments in the app yet, but their members
        # still hear about every shilling that leaves the bank, the same day.
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
    amount, day = _kes(ud.bank_txn.amount_cents, ud), ud.bank_txn.value_date.strftime("%d %b")
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
        # Approving after the money has gone is not approval: that is for the members.
        if timezone.localdate(p.created_at) > ud.bank_txn.value_date:
            raise Problem("approved_after", "This payment was proposed after the money left. "
                                            "Explain it to the members instead.",
                          status.HTTP_409_CONFLICT)
        _write_payout(p, ud.bank_txn, {"resolves_unapproved": str(ud.entry_id)})
        ud.state, ud.linked_proposal = UnapprovedDebit.State.LINKED, p
        ud.resolved_by, ud.resolved_at = by, timezone.now()
        ud.save(update_fields=["state", "linked_proposal", "resolved_by", "resolved_at"])
    return ud


def explain(ud: UnapprovedDebit, by: Membership, text: str) -> UnapprovedDebit:
    """An official explains an unapproved withdrawal. That does not close it: the members
    vote on the explanation (the official who gave it does not vote), and the vote and its
    question go on the ledger. Until they accept it the chama is in breach and nobody can
    move money (protection plan 2026-10-20)."""
    from apps.votes import services as votes

    _official(by)
    text = (text or "").strip()[:500]
    if not text:
        raise Problem("explanation_required", "Explain where the money went.")
    with transaction.atomic():
        ud = UnapprovedDebit.objects.select_for_update().select_related("bank_txn").get(pk=ud.pk)
        if ud.state != UnapprovedDebit.State.OPEN:
            raise Problem("not_open", "This debit has already been dealt with.",
                          status.HTTP_409_CONFLICT)
        if ud.explanation:
            raise Problem("already_explained", "The members are voting on an explanation.",
                          status.HTTP_409_CONFLICT)
        ud.explanation, ud.resolved_by = text, by
        ud.save(update_fields=["explanation", "resolved_by"])
        version = version_at(ud.chama, timezone.localdate())
        votes.open_vote(
            ud.chama, subject=f"payouts.debit:{ud.id}",
            question=(f"{_kes(ud.bank_txn.amount_cents, ud)} left the bank on "
                      f"{ud.bank_txn.value_date:%d %b} with no approval. {by.member_code} "
                      f"explains: {text[:180]}. Accept this explanation?"),
            rules=version.rules if version else {},
            closes_at=timezone.now() + timedelta(days=3), exclude=[by.id])
    return ud


def on_debit_vote_closed(vote) -> None:
    """The members decided on an explanation (votes.services._close)."""
    ud = UnapprovedDebit.objects.select_for_update().get(pk=vote.subject.split(":", 1)[1])
    if ud.state != UnapprovedDebit.State.OPEN:
        return
    if vote.state == "passed":
        ud.state, ud.resolved_at = UnapprovedDebit.State.EXPLAINED, timezone.now()
        ud.save(update_fields=["state", "resolved_at"])
    else:
        # Not accepted: the withdrawal stays open, in breach, and can be explained again.
        ud.explanation, ud.resolved_by = "", None
        ud.save(update_fields=["explanation", "resolved_by"])


def open_breaches(chama: Chama) -> int:
    """Withdrawals the members have not accepted. While any is open, money is frozen."""
    return UnapprovedDebit.objects.filter(chama=chama, state=UnapprovedDebit.State.OPEN).count()
