"""Loans with guarantors (5.8). See docs/flows/loans.md.

Everything here is behind the "loans" flag, which stays off until the advocate confirms
that recording a chama's own loans does not make JiraniChama a lender under Google Play's
policy (decision 4), and needs a ``loans`` section in the chama's rules.

A member asks for a loan; the app shows eligibility, the limit and the full schedule from
the rules first. Guarantors accept with an SMS code naming the amount and their share. The
loan then becomes a money-out proposal (kind "loan") with the officials' approvals and, above
the vote level, a members' vote. When the bank debit matches, a loan_disbursed entry starts
the schedule. Repayments arrive like contributions and are split by the rules; interest is
charged by entries on each due date, never by changing a balance.
"""

import hashlib
from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import status

from apps.chamas.models import OFFICIAL_ROLES, Chama, Membership
from apps.contributions.projections import dues, position
from apps.core.canonical import canonical_bytes
from apps.core.problems import Problem
from apps.devices import signing
from apps.devices.models import SigningChallenge
from apps.fines.models import Fine
from apps.flags.models import is_enabled
from apps.ledger import services as ledger
from apps.ledger.models import LedgerEntry
from apps.notify.sms import SmsFailed, send_sms
from apps.rules import calendar
from apps.rules.models import version_at

from . import schedule as sched
from .models import Guarantee, Loan, LoanReminder

GUARANTEE_HOURS = 72
LOAN_REQUEST_DAYS = 7


def _kes(cents: int) -> str:
    return f"KES {cents / 100:,.0f}" if cents % 100 == 0 else f"KES {cents / 100:,.2f}"


def _name(m: Membership) -> str:
    return m.user.display_name or m.member_code


def _sms(m: Membership, text: dict) -> None:
    try:
        send_sms(m.user.phone, text.get(m.user.language, text["en"]))
    except SmsFailed:
        pass


def enabled(chama: Chama, today: date | None = None) -> dict | None:
    """The chama's loan rules when loans are on for it, else None."""
    if not is_enabled("loans", chama.id):
        return None
    version = version_at(chama, today or timezone.localdate())
    return sched.settings_from(version.rules) if version else None


def _require_enabled(chama: Chama) -> dict:
    s = enabled(chama)
    if s is None:
        raise Problem("loans_off", "Loans are not switched on for this chama.",
                      status.HTTP_409_CONFLICT)
    return s


# --- limits ---------------------------------------------------------------------------------

def savings_cents(m: Membership, today: date) -> int:
    return position(m, today).contributed_cents


def guaranteed_cents(m: Membership, exclude_loan=None) -> int:
    qs = Guarantee.objects.filter(guarantor=m, state__in=Guarantee.HOLDING,
                                  loan__state__in=Loan.OPEN)
    if exclude_loan is not None:
        qs = qs.exclude(loan=exclude_loan)
    return qs.aggregate(t=Sum("share_cents"))["t"] or 0


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month - (1 if b.day < a.day else 0)


def _reason(code: str, rule: str, message: str) -> dict:
    return {"code": code, "rule": rule, "message": message}


def quote(chama: Chama, m: Membership, *, amount_cents: int, term_months: int,
          exclude_loan=None) -> dict:
    """What the app shows before a request: eligibility, the limit, the terms and the
    whole schedule as if paid out today."""
    s = _require_enabled(chama)
    today = timezone.localdate()
    savings = savings_cents(m, today)
    limit = max(savings * s["savings_multiple_pct"] // 100
                - guaranteed_cents(m, exclude_loan), 0)
    reasons = []
    if m.joined_on is None or _months_between(m.joined_on, today) < s["min_months"]:
        reasons.append(_reason("too_new", sched.REF_ELIGIBILITY,
                               f"Members borrow after {s['min_months']} months."))
    current = calendar.period_of(today)
    if any(d.outstanding_cents > 0 and d.period < current for d in dues(m, today)):
        reasons.append(_reason("in_arrears", sched.REF_ELIGIBILITY,
                               "Contributions are behind; catch up first."))
    open_loans = Loan.objects.filter(borrower=m, state__in=Loan.OPEN)
    if exclude_loan is not None:
        open_loans = open_loans.exclude(pk=exclude_loan.pk)
    if open_loans.exists():
        reasons.append(_reason("loan_open", sched.REF_ELIGIBILITY,
                               "One loan at a time."))
    if amount_cents <= 0:
        reasons.append(_reason("invalid_amount", sched.REF_ELIGIBILITY, "Enter the amount."))
    elif amount_cents > limit:
        reasons.append(_reason("over_limit", sched.REF_ELIGIBILITY,
                               f"The most you can borrow now is {_kes(limit)}."))
    if not 1 <= term_months <= s["max_term_months"]:
        reasons.append(_reason("term_too_long", sched.REF_ELIGIBILITY,
                               f"Loans run 1 to {s['max_term_months']} months."))
    rows = (sched.build(amount_cents=amount_cents, term_months=term_months, s=s, start=today)
            if amount_cents > 0 and 1 <= term_months <= s["max_term_months"] else [])
    n = s["guarantors"]
    return {
        "eligible": not reasons, "reasons": reasons, "savings_cents": savings,
        "limit_cents": limit, "terms": s, "guarantors_needed": n,
        "share_cents": amount_cents // n if n else 0,
        "schedule": [r.as_dict() for r in rows],
        "total_interest_cents": sum(r.interest_cents for r in rows),
        "total_cents": sum(r.total_cents for r in rows),
    }


# --- requesting and guarantors --------------------------------------------------------------

def _guarantor_ok(loan_amount: int, share: int, g: Membership, borrower: Membership,
                  today: date, loan=None) -> None:
    if g.chama_id != borrower.chama_id or not g.is_current or g.id == borrower.id:
        raise Problem("invalid_guarantor", "Choose guarantors who are members of this chama.")
    free = savings_cents(g, today) - guaranteed_cents(g, exclude_loan=loan)
    if free < share:
        raise Problem("guarantor_cannot_cover",
                      f"{_name(g)}'s savings cannot cover a {_kes(share)} share.",
                      status.HTTP_409_CONFLICT)


def _ask_guarantor(g: Guarantee) -> None:
    loan = g.loan
    who, amount, share = _name(loan.borrower), _kes(loan.amount_cents), _kes(g.share_cents)
    _sms(g.guarantor, {
        "en": f"{loan.chama.name}: {who} asks you to guarantee a loan of {amount} (your "
              f"share {share}). Answer in the JiraniChama app within 3 days.",
        "sw": f"{loan.chama.name}: {who} anakuomba udhamini wa mkopo wa {amount} (sehemu "
              f"yako {share}). Jibu kwenye programu ya JiraniChama ndani ya siku 3.",
    })


def request(chama: Chama, m: Membership, *, amount_cents: int, term_months: int,
            purpose: str, guarantor_ids: list) -> Loan:
    purpose = (purpose or "").strip()[:200]
    if not purpose:
        raise Problem("purpose_required", "Say what the loan is for.")
    q = quote(chama, m, amount_cents=amount_cents, term_months=term_months)
    if not q["eligible"]:
        r = q["reasons"][0]
        raise Problem(r["code"], r["message"], status.HTTP_409_CONFLICT, reasons=q["reasons"])
    ids = list(dict.fromkeys(str(i) for i in guarantor_ids or []))
    if len(ids) != q["guarantors_needed"]:
        raise Problem("guarantors_needed",
                      f"Choose {q['guarantors_needed']} different guarantors.")
    today = timezone.localdate()
    guarantors = list(Membership.objects.filter(pk__in=ids).select_related("user"))
    if len(guarantors) != len(ids):
        raise Problem("invalid_guarantor", "Choose guarantors who are members of this chama.")
    for g in guarantors:
        _guarantor_ok(amount_cents, q["share_cents"], g, m, today)
    now = timezone.now()
    version = version_at(chama, today)
    with transaction.atomic():
        loan = Loan.objects.create(
            chama=chama, borrower=m, amount_cents=amount_cents, term_months=term_months,
            purpose=purpose, rule_version_id=version.id, terms=q["terms"],
            schedule=q["schedule"], expires_at=now + timedelta(days=LOAN_REQUEST_DAYS))
        made = [Guarantee.objects.create(loan=loan, guarantor=g, share_cents=q["share_cents"],
                                         expires_at=now + timedelta(hours=GUARANTEE_HOURS))
                for g in guarantors]
        if not made:
            _to_approval(loan)
    for g in made:
        transaction.on_commit(lambda g=g: _ask_guarantor(g))
    loan.refresh_from_db()
    return loan


def _guarantee_hash(g: Guarantee) -> str:
    return hashlib.sha256(canonical_bytes({
        "loan": str(g.loan_id), "amount_cents": g.loan.amount_cents,
        "share_cents": g.share_cents, "guarantor": str(g.guarantor_id)})).hexdigest()


def _guarantee_text(g: Guarantee, language: str) -> str:
    who, amount, share = _name(g.loan.borrower), _kes(g.loan.amount_cents), _kes(g.share_cents)
    if language == "sw":
        return f"KUDHAMINI {amount} kwa {who} (sehemu yako {share})"
    return f"GUARANTEE {amount} for {who} (your share {share})"


def _require_answerable(g: Guarantee, by: Membership) -> None:
    if g.guarantor_id != by.id:
        raise Problem("not_allowed", "Only the guarantor answers this.",
                      status.HTTP_403_FORBIDDEN)
    if (g.state != Guarantee.State.REQUESTED or g.expires_at <= timezone.now()
            or g.loan.state != Loan.State.AWAITING_GUARANTORS):
        raise Problem("already_answered", "This request is no longer open.",
                      status.HTTP_409_CONFLICT)


def start_accept(g: Guarantee, by: Membership) -> SigningChallenge:
    _require_answerable(g, by)
    return signing.issue(user=by.user, purpose=SigningChallenge.Purpose.GUARANTEE_LOAN,
                         subject=f"loans.guarantee:{g.id}", content_hash=_guarantee_hash(g),
                         action_text=_guarantee_text(g, by.user.language))


def accept(g: Guarantee, by: Membership, *, challenge_id, sms_code: str) -> Guarantee:
    _require_answerable(g, by)
    _guarantor_ok(g.loan.amount_cents, g.share_cents, by, g.loan.borrower,
                  timezone.localdate(), loan=g.loan)
    signing.confirm(user=by.user, challenge_id=challenge_id, code=sms_code,
                    purpose=SigningChallenge.Purpose.GUARANTEE_LOAN,
                    subject=f"loans.guarantee:{g.id}", content_hash=_guarantee_hash(g))
    with transaction.atomic():
        loan = Loan.objects.select_for_update().get(pk=g.loan_id)
        g = Guarantee.objects.select_for_update().get(pk=g.pk)
        _require_answerable(g, by)
        g.state, g.challenge_id, g.answered_at = (Guarantee.State.ACCEPTED, challenge_id,
                                                  timezone.now())
        g.save(update_fields=["state", "challenge_id", "answered_at"])
        if loan.guarantees.filter(state=Guarantee.State.ACCEPTED).count() \
                >= loan.terms["guarantors"]:
            _to_approval(loan)
    return g


def decline(g: Guarantee, by: Membership) -> Guarantee:
    _require_answerable(g, by)
    g.state, g.answered_at = Guarantee.State.DECLINED, timezone.now()
    g.save(update_fields=["state", "answered_at"])
    loan = g.loan
    _sms(loan.borrower, {
        "en": f"{loan.chama.name}: {_name(by)} declined to guarantee your loan. Choose "
              "someone else in the app.",
        "sw": f"{loan.chama.name}: {_name(by)} amekataa kudhamini mkopo wako. Chagua mtu "
              "mwingine kwenye programu.",
    })
    return g


def replace_guarantor(loan: Loan, by: Membership, *, guarantee_id, membership_id) -> Loan:
    """After a decline or no answer, the borrower picks someone else."""
    if loan.borrower_id != by.id:
        raise Problem("not_allowed", "Only the borrower changes guarantors.",
                      status.HTTP_403_FORBIDDEN)
    if loan.state != Loan.State.AWAITING_GUARANTORS:
        raise Problem("loan_state", "Guarantors are chosen before approval.",
                      status.HTTP_409_CONFLICT)
    old = loan.guarantees.filter(pk=guarantee_id).first()
    if old is None or old.state not in (Guarantee.State.DECLINED, Guarantee.State.EXPIRED):
        raise Problem("not_replaceable", "Only a guarantor who declined or did not answer "
                                         "can be replaced.", status.HTTP_409_CONFLICT)
    new = Membership.objects.filter(pk=membership_id).select_related("user").first()
    if new is None or loan.guarantees.filter(guarantor=new,
                                             state__in=Guarantee.HOLDING).exists():
        raise Problem("invalid_guarantor", "Choose another member of this chama.")
    _guarantor_ok(loan.amount_cents, old.share_cents, new, loan.borrower, timezone.localdate())
    # The declined or lapsed guarantee stays as history; a new one takes its place.
    g = Guarantee.objects.create(loan=loan, guarantor=new, share_cents=old.share_cents,
                                 expires_at=timezone.now() + timedelta(hours=GUARANTEE_HOURS))
    transaction.on_commit(lambda: _ask_guarantor(g))
    return loan


def cancel(loan: Loan, by: Membership) -> Loan:
    if loan.borrower_id != by.id:
        raise Problem("not_allowed", "Only the borrower cancels the request.",
                      status.HTTP_403_FORBIDDEN)
    if loan.state != Loan.State.AWAITING_GUARANTORS:
        raise Problem("loan_state", "A request is cancelled while guarantors are answering.",
                      status.HTTP_409_CONFLICT)
    _end(loan, Loan.State.CANCELLED, "Cancelled by the borrower.")
    return loan


def _end(loan: Loan, state: str, reason: str) -> None:
    loan.state, loan.state_reason = state, reason[:300]
    loan.save(update_fields=["state", "state_reason"])
    for g in loan.guarantees.filter(state__in=[Guarantee.State.REQUESTED,
                                               Guarantee.State.ACCEPTED]):
        g.state = (Guarantee.State.EXPIRED if g.state == Guarantee.State.REQUESTED
                   else Guarantee.State.RELEASED)
        g.save(update_fields=["state"])


# --- approval and disbursement --------------------------------------------------------------

def _to_approval(loan: Loan) -> None:
    """All guarantors are in: the limit is checked again and a money-out proposal opens.
    Called with the loan locked."""
    from apps.payouts import services as payouts
    from apps.payouts.models import Proposal

    q = quote(loan.chama, loan.borrower, amount_cents=loan.amount_cents,
              term_months=loan.term_months, exclude_loan=loan)
    if not q["eligible"]:
        _end(loan, Loan.State.DECLINED, q["reasons"][0]["message"])
        return
    try:
        p = payouts.propose(loan.chama, loan.borrower, kind=Proposal.Kind.LOAN,
                            amount_cents=loan.amount_cents, payee=loan.borrower,
                            purpose=f"Loan: {loan.purpose}"[:200])
    except Problem as exc:
        _end(loan, Loan.State.DECLINED, str(exc.detail))
        return
    loan.proposal = p
    loan.state = Loan.State.AWAITING_APPROVAL
    loan.save(update_fields=["proposal", "state"])
    sync(loan)


def sync(loan: Loan) -> Loan:
    """Follow the money-out proposal: approved, refused, rejected, expired or cancelled."""
    from apps.payouts.models import Proposal

    if loan.proposal_id is None or loan.state not in (Loan.State.AWAITING_APPROVAL,
                                                      Loan.State.APPROVED):
        return loan
    p = Proposal.objects.get(pk=loan.proposal_id)
    if p.state in (Proposal.State.APPROVED, Proposal.State.PAID_PENDING_MATCH) \
            and loan.state != Loan.State.APPROVED:
        loan.state = Loan.State.APPROVED
        loan.save(update_fields=["state"])
    elif p.state in (Proposal.State.REFUSED, Proposal.State.REJECTED):
        _end(loan, Loan.State.DECLINED, p.state_reason or "The approvals were not given.")
    elif p.state == Proposal.State.EXPIRED:
        _end(loan, Loan.State.EXPIRED, p.state_reason)
    elif p.state == Proposal.State.CANCELLED:
        _end(loan, Loan.State.CANCELLED, p.state_reason or "The proposal was cancelled.")
    return loan


def on_paid(p) -> None:
    """The bank debit matched (payouts._write_payout): the schedule starts that day."""
    loan = Loan.objects.select_for_update().filter(proposal=p).first()
    if loan is None:
        return
    start = p.matched_txn.value_date if p.matched_txn_id else timezone.localdate()
    rows = sched.build(amount_cents=loan.amount_cents, term_months=loan.term_months,
                       s=loan.terms, start=start)
    loan.schedule = [r.as_dict() for r in rows]
    loan.disbursed_on, loan.state = start, Loan.State.REPAYING
    loan.save(update_fields=["schedule", "disbursed_on", "state"])
    first = rows[0]
    _sms(loan.borrower, {
        "en": f"{loan.chama.name}: your loan of {_kes(loan.amount_cents)} is paid out. First "
              f"instalment {_kes(first.total_cents)} on {first.due:%d %b}, with your member "
              "code as the reference.",
        "sw": f"{loan.chama.name}: mkopo wako wa {_kes(loan.amount_cents)} umelipwa. Awamu "
              f"ya kwanza {_kes(first.total_cents)} tarehe {first.due:%d %b}, kwa namba yako "
              "ya mwanachama kama kumbukumbu.",
    })


# --- balances and repayment -----------------------------------------------------------------

def _entries(loan: Loan):
    return LedgerEntry.objects.filter(chama_id=loan.chama_id, decision__loan_id=str(loan.id))


def _net(loan: Loan, type_: str) -> int:
    """The total of a loan's entries of one type, less any reversals of them."""
    qs = _entries(loan).filter(type=type_)
    total = qs.aggregate(t=Sum("amount_cents"))["t"] or 0
    reversed_ = LedgerEntry.objects.filter(type="reversal", reverses__in=qs) \
        .aggregate(t=Sum("amount_cents"))["t"] or 0
    return total + reversed_


def balance(loan: Loan, on: date | None = None) -> dict:
    on = on or timezone.localdate()
    rows = sched.from_dicts(loan.schedule)
    interest = _net(loan, "loan_interest")
    repaid = _net(loan, "loan_repayment")
    principal = loan.amount_cents if loan.disbursed_on else 0
    due_through = sum(r.total_cents for r in rows if loan.disbursed_on and r.due <= on)
    return {"principal_cents": principal, "interest_charged_cents": interest,
            "repaid_cents": repaid, "outstanding_cents": max(principal + interest - repaid, 0),
            "due_now_cents": max(due_through - repaid, 0),
            "next": next((r.as_dict() for r in rows if loan.disbursed_on and r.due > on),
                         None)}


def running_loan(m: Membership) -> Loan | None:
    return Loan.objects.filter(borrower=m, state__in=Loan.RUNNING).first()


def repayment_cents(m: Membership, *, spare_cents: int, amount_cents: int,
                    value_date: date) -> tuple[Loan | None, int, str]:
    """How much of a payment goes to the member's loan, and from which part of it.
    ``spare_cents`` is what the contribution rules would put in advance or credit."""
    loan = running_loan(m)
    if loan is None or enabled(loan.chama, value_date) is None:
        return None, 0, ""
    b = balance(loan, value_date)
    want = min(b["outstanding_cents"], max(b["due_now_cents"], 0))
    if loan.terms["contribution_first"]:
        return loan, min(spare_cents, want), "spare"
    return loan, min(amount_cents, want), "first"


def write_repayment(loan: Loan, *, txn, member, amount_cents: int, version, base: dict):
    entry = ledger.append(
        chama=loan.chama, type="loan_repayment", source="bank_txn", source_id=txn.id,
        member=member, amount_cents=amount_cents, rule_version_id=version.id,
        rule_refs=[sched.REF_INTEREST],
        decision={**base, "loan_id": str(loan.id)})
    transaction.on_commit(lambda: settle(loan.pk))
    return entry


def settle(loan_id) -> None:
    """After a repayment: repaid when nothing is owed and the schedule has run, or when
    every instalment is paid early. Guarantors are released and told."""
    with transaction.atomic():
        loan = Loan.objects.select_for_update().get(pk=loan_id)
        if loan.state not in Loan.RUNNING:
            return
        b = balance(loan)
        rows = sched.from_dicts(loan.schedule)
        total = loan.amount_cents + sum(r.interest_cents for r in rows)
        if b["repaid_cents"] < loan.amount_cents + b["interest_charged_cents"] or \
                (b["repaid_cents"] < total and rows[-1].due > timezone.localdate()):
            return
        guarantors = [g.guarantor for g in loan.guarantees.filter(
            state__in=[Guarantee.State.ACCEPTED, Guarantee.State.CALLED])
            .select_related("guarantor__user")]
        Guarantee.objects.filter(loan=loan, state=Guarantee.State.CALLED) \
            .update(state=Guarantee.State.RELEASED)
        _end(loan, Loan.State.REPAID, "Repaid in full.")
    for m in [loan.borrower, *guarantors]:
        _sms(m, {"en": f"{loan.chama.name}: the loan of {_kes(loan.amount_cents)} to "
                       f"{_name(loan.borrower)} is repaid in full. Guarantees are released.",
                 "sw": f"{loan.chama.name}: mkopo wa {_kes(loan.amount_cents)} kwa "
                       f"{_name(loan.borrower)} umelipwa wote. Udhamini umeachiliwa."})


# --- the daily run --------------------------------------------------------------------------

def _charge_interest(loan: Loan, today: date) -> int:
    n = 0
    charged = set(_entries(loan).filter(type="loan_interest")
                  .values_list("decision__instalment", flat=True))
    for r in sched.from_dicts(loan.schedule):
        if r.due > today or r.no in charged or r.interest_cents <= 0:
            continue
        ledger.append(chama=loan.chama, type="loan_interest", source="scheduler",
                      source_id=loan.id, member=loan.borrower, amount_cents=r.interest_cents,
                      period=f"{r.due:%Y-%m}", rule_version_id=loan.rule_version_id,
                      rule_refs=[sched.REF_INTEREST],
                      decision={"loan_id": str(loan.id), "instalment": r.no,
                                "due": r.due.isoformat(), "rate_bp": loan.terms["monthly_rate_bp"],
                                "interest": loan.terms["interest"]})
        n += 1
    return n


def missed(loan: Loan, today: date) -> list:
    """Instalments past their grace that the repayments so far do not cover."""
    repaid = _net(loan, "loan_repayment")
    out, running = [], 0
    for r in sched.from_dicts(loan.schedule):
        running += r.total_cents
        if r.due + timedelta(days=loan.terms["grace_days"]) < today and repaid < running:
            out.append(r)
    return out


def _penalise(loan: Loan, r) -> None:
    amount = loan.terms["late_penalty_cents"]
    if amount <= 0:
        return
    period = f"{r.due:%Y-%m}"
    if Fine.objects.filter(membership=loan.borrower, rule_ref=sched.REF_PENALTY,
                           period=period, meeting__isnull=True).exists():
        return
    try:
        with transaction.atomic():
            entry = ledger.append(
                chama=loan.chama, type="fine_raised", source="scheduler", source_id=loan.id,
                member=loan.borrower, amount_cents=amount, period=period,
                rule_version_id=loan.rule_version_id, rule_refs=[sched.REF_PENALTY],
                decision={"cause": "loan_instalment_missed", "loan_id": str(loan.id),
                          "instalment": r.no, "due": r.due.isoformat()})
            Fine.objects.create(chama=loan.chama, membership=loan.borrower,
                                rule_ref=sched.REF_PENALTY, period=period,
                                amount_cents=amount, raised_entry=entry)
    except IntegrityError:
        pass


def _remind(loan: Loan, no: int, kind: str) -> bool:
    try:
        with transaction.atomic():
            LoanReminder.objects.create(loan=loan, instalment=no, kind=kind)
        return True
    except IntegrityError:
        return False


def _default(loan: Loan) -> None:
    loan.state = Loan.State.DEFAULTED
    loan.state_reason = f"{loan.terms['default_after_missed']} instalments missed."
    loan.save(update_fields=["state", "state_reason"])
    loan.guarantees.filter(state=Guarantee.State.ACCEPTED).update(state=Guarantee.State.CALLED)
    if not _remind(loan, 0, "default"):
        return
    owed = _kes(balance(loan)["outstanding_cents"])
    text = {"en": f"{loan.chama.name}: the loan to {_name(loan.borrower)} is in default with "
                  f"{owed} owed (rule 12.4). Officials will propose recovery from savings, "
                  "then from guarantors in proportion to their shares.",
            "sw": f"{loan.chama.name}: mkopo wa {_name(loan.borrower)} umeshindwa kulipwa, "
                  f"deni {owed} (kanuni 12.4). Viongozi watapendekeza kurejesha kutoka akiba, "
                  "kisha kwa wadhamini kulingana na sehemu zao."}
    people = {loan.borrower.id: loan.borrower}
    for g in loan.guarantees.filter(state=Guarantee.State.CALLED).select_related(
            "guarantor__user"):
        people[g.guarantor_id] = g.guarantor
    for m in Membership.objects.filter(chama=loan.chama, role__in=OFFICIAL_ROLES,
                                       state=Membership.State.ACTIVE).select_related("user"):
        people[m.id] = m
    for m in people.values():
        _sms(m, text)


def run_daily(today: date | None = None) -> dict:
    """Once a day: expire unanswered guarantees and stale requests, follow proposals,
    charge interest on due dates, raise penalties, mark arrears and default, remind."""
    today = today or timezone.localdate()
    now = timezone.now()
    stats = {"interest": 0, "reminders": 0}
    Guarantee.objects.filter(state=Guarantee.State.REQUESTED, expires_at__lte=now,
                             loan__state=Loan.State.AWAITING_GUARANTORS) \
        .update(state=Guarantee.State.EXPIRED)
    for loan in Loan.objects.filter(state=Loan.State.AWAITING_GUARANTORS, expires_at__lte=now):
        _end(loan, Loan.State.EXPIRED, "Guarantors were not complete within 7 days.")
    for loan in Loan.objects.filter(state__in=[Loan.State.AWAITING_APPROVAL,
                                               Loan.State.APPROVED]):
        sync(loan)
    for loan in Loan.objects.filter(state__in=Loan.RUNNING).select_related(
            "chama", "borrower__user"):
        with transaction.atomic():
            loan = Loan.objects.select_for_update().get(pk=loan.pk)
            stats["interest"] += _charge_interest(loan, today)
            gone = missed(loan, today)
            for r in gone:
                _penalise(loan, r)
            if len(gone) >= loan.terms["default_after_missed"]:
                if loan.state != Loan.State.DEFAULTED:
                    _default(loan)
            elif gone and loan.state == Loan.State.REPAYING:
                loan.state = Loan.State.IN_ARREARS
                loan.save(update_fields=["state"])
            elif not gone and loan.state == Loan.State.IN_ARREARS:
                loan.state = Loan.State.REPAYING
                loan.save(update_fields=["state"])
        for r in sched.from_dicts(loan.schedule):
            kind = ("before" if r.due - timedelta(days=3) == today
                    else "due" if r.due == today else None)
            if kind and _remind(loan, r.no, kind):
                _sms(loan.borrower, {
                    "en": f"{loan.chama.name}: loan instalment {_kes(r.total_cents)} due "
                          f"{r.due:%d %b}. Pay with your member code as the reference.",
                    "sw": f"{loan.chama.name}: awamu ya mkopo {_kes(r.total_cents)} inadaiwa "
                          f"{r.due:%d %b}. Lipa kwa namba yako ya mwanachama.",
                })
                stats["reminders"] += 1
        settle(loan.pk)
    return stats
