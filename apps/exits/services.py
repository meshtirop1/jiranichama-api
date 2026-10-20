"""Leaving the chama, and the death of a member (5.13). See docs/flows/exits.md.

What a member gets back is worked out from the rules: their savings (contributions,
opening savings and credit), less unpaid fines, less an open loan, less any exit
deduction. Fines and the loan are settled from the savings inside the books (entries with
source "exit", which never touch the bank); only the settlement is paid out, as money out
the officials approve. A loan larger than the savings calls the guarantors.
"""

import hashlib
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.chamas.models import OFFICIAL_ROLES, Chama, Membership
from apps.contributions.projections import position
from apps.core import money
from apps.core.canonical import canonical_bytes
from apps.core.problems import Problem
from apps.devices import signing
from apps.devices.models import SigningChallenge
from apps.fines.models import Fine
from apps.ledger import services as ledger
from apps.rules.models import version_at

from .models import DeathConfirmation, Exit

DEFAULTS = {"notice_days": 30, "deduction_pct": 0, "death_benefit_cents": 0}
REF_EXIT = "16.1"

# A reported death waits this long for the member to say it is not true, and for a second
# official to confirm it, before the member is marked deceased and anything is settled.
DEATH_OBJECTION_DAYS = 14


def settings_from(rules: dict) -> dict:
    return {**DEFAULTS, **(rules.get("exit") or {})}


def _kes(cents: int, of=None) -> str:
    return money.fmt(cents, of)


def quote(m: Membership, *, death: bool = False) -> dict:
    """What the member (or their next of kin) gets back, and why."""
    from apps.loans.services import balance, running_loan

    today = timezone.localdate()
    s = settings_from(version_at(m.chama, today).rules)
    p = position(m, today)
    savings = p.contributed_cents + p.credit_cents
    loan = running_loan(m)
    loan_cents = balance(loan, today)["outstanding_cents"] if loan else 0
    deduction = 0 if death else savings * s["deduction_pct"] // 100
    settlement = savings - p.fines_cents - loan_cents - deduction
    return {"savings_cents": savings, "fines_cents": p.fines_cents, "loan_cents": loan_cents,
            "deduction_pct": 0 if death else s["deduction_pct"], "deduction_cents": deduction,
            "settlement_cents": max(settlement, 0), "shortfall_cents": max(-settlement, 0),
            "notice_days": 0 if death else s["notice_days"],
            "exit_on": (today if death else today + timedelta(days=s["notice_days"]))
            .isoformat(),
            "death_benefit_cents": s["death_benefit_cents"] if death else 0}


def _hash(m: Membership, q: dict) -> str:
    return hashlib.sha256(canonical_bytes({"membership": str(m.id), "leave": True,
                                           "settlement_cents": q["settlement_cents"]})
                          ).hexdigest()


def _summary(m: Membership, q: dict, language: str) -> str:
    if language == "sw":
        return f"KUONDOKA {m.chama.name} na kurudishiwa {_kes(q['settlement_cents'], m)}"
    return f"LEAVE {m.chama.name} and get back {_kes(q['settlement_cents'], m)}"


def _require_can_leave(m: Membership) -> None:
    if m.state != Membership.State.ACTIVE:
        raise Problem("not_allowed", "Only an active member can leave.",
                      status.HTTP_409_CONFLICT)
    if m.role in OFFICIAL_ROLES:
        raise Problem("official_must_hand_over", "An official hands over the role in an "
                                                 "election before leaving.",
                      status.HTTP_409_CONFLICT)


def start_leave(m: Membership) -> SigningChallenge:
    _require_can_leave(m)
    q = quote(m)
    return signing.issue(user=m.user, purpose=SigningChallenge.Purpose.LEAVE_CHAMA,
                         subject=f"chamas.membership:{m.id}", content_hash=_hash(m, q),
                         action_text=_summary(m, q, m.user.language))


def leave(m: Membership, *, challenge_id, sms_code: str) -> Exit:
    _require_can_leave(m)
    q = quote(m)
    signing.confirm(user=m.user, challenge_id=challenge_id, code=sms_code,
                    purpose=SigningChallenge.Purpose.LEAVE_CHAMA,
                    subject=f"chamas.membership:{m.id}", content_hash=_hash(m, q))
    with transaction.atomic():
        entry = ledger.append(chama=m.chama, type="membership", source="exit", member=m,
                              rule_refs=[REF_EXIT],
                              decision={"change": "exiting", "exit_on": q["exit_on"],
                                        "quote": q})
        Membership.objects.filter(pk=m.pk).update(state=Membership.State.EXITING)
        e = Exit.objects.create(chama=m.chama, membership=m, kind=Exit.Kind.LEAVE,
                                recorded_by=m, exit_on=date.fromisoformat(q["exit_on"]),
                                quote=q, entry=entry)
    if q["notice_days"] == 0:
        settle_due(m.chama)
        e.refresh_from_db()
    return e


def cancel(e: Exit, by: Membership) -> Exit:
    if e.kind == Exit.Kind.DEATH and by.id == e.membership_id:
        return deny_death(e)  # "I am alive"
    if by.id != e.membership_id or e.state != Exit.State.NOTICE:
        raise Problem("not_allowed", "Only the member can stay, and only during the notice.",
                      status.HTTP_409_CONFLICT)
    with transaction.atomic():
        e.state = Exit.State.CANCELLED
        e.save(update_fields=["state"])
        Membership.objects.filter(pk=by.pk).update(state=Membership.State.ACTIVE)
        ledger.append(chama=e.chama, type="membership", source="exit", member=by,
                      rule_refs=[REF_EXIT], decision={"change": "stays", "exit": str(e.id)})
    return e


def record_death(chama: Chama, by: Membership, member: Membership, *, died_on,
                 next_of_kin_name: str, next_of_kin_phone: str, document) -> Exit:
    """An official reports that a member has died. This is only a report: the member is
    not locked out, they are told by SMS and can say it is not true, and nothing happens to
    their money until a second official confirms it and the objection days have passed."""
    if by.role not in OFFICIAL_ROLES:
        raise Problem("not_allowed", "An official records a death.", status.HTTP_403_FORBIDDEN)
    if member.pk == by.pk:
        raise Problem("not_allowed", "Choose another member.", status.HTTP_403_FORBIDDEN)
    if Exit.objects.filter(membership=member, kind=Exit.Kind.DEATH,
                           state__in=[Exit.State.NOTICE, Exit.State.SETTLING]).exists():
        raise Problem("already_reported", "This death has already been reported.",
                      status.HTTP_409_CONFLICT)
    if member.chama_id != chama.id or member.state not in (Membership.State.ACTIVE,
                                                           Membership.State.PROBATION,
                                                           Membership.State.EXITING):
        raise Problem("invalid_member", "Choose a member of this chama.")
    if not (next_of_kin_name or "").strip() or not (next_of_kin_phone or "").strip():
        raise Problem("next_of_kin_required", "Give the next of kin's name and phone.")
    if document is None:
        raise Problem("document_required", "Add a photo of the burial permit or death "
                                           "certificate.")
    digest = hashlib.sha256()
    for chunk in document.chunks():
        digest.update(chunk)
    document.seek(0)
    q = quote(member, death=True)
    with transaction.atomic():
        entry = ledger.append(chama=chama, type="membership", source="exit", member=member,
                              rule_refs=[REF_EXIT],
                              decision={"change": "death_reported", "died_on": str(died_on),
                                        "recorded_by": by.member_code,
                                        "objection_days": DEATH_OBJECTION_DAYS,
                                        "document_sha256": digest.hexdigest(), "quote": q})
        e = Exit(chama=chama, membership=member, kind=Exit.Kind.DEATH, recorded_by=by,
                 exit_on=timezone.localdate() + timedelta(days=DEATH_OBJECTION_DAYS),
                 quote=q, died_on=died_on, document_sha256=digest.hexdigest(), entry=entry)
        e.set_next_of_kin(next_of_kin_name.strip()[:120], next_of_kin_phone.strip()[:20])
        e.document.save(f"{member.id}.jpg", document, save=False)
        e.save()
    _tell_reported(e)
    return e


def confirm_death(e: Exit, by: Membership) -> Exit:
    """A second official confirms the report. Never the one who reported it."""
    if e.kind != Exit.Kind.DEATH or e.state != Exit.State.NOTICE:
        raise Problem("not_open", "This report is no longer open.", status.HTTP_409_CONFLICT)
    if by.chama_id != e.chama_id or by.role not in OFFICIAL_ROLES \
            or by.state != Membership.State.ACTIVE:
        raise Problem("not_allowed", "Another official confirms a death.",
                      status.HTTP_403_FORBIDDEN)
    if by.pk in (e.recorded_by_id, e.membership_id):
        raise Problem("not_allowed", "The official who reported a death cannot confirm it.",
                      status.HTTP_403_FORBIDDEN)
    with transaction.atomic():
        _, created = DeathConfirmation.objects.get_or_create(exit=e, by=by)
        if created:
            ledger.append(chama=e.chama, type="membership", source="exit",
                          member=e.membership, rule_refs=[REF_EXIT],
                          decision={"change": "death_confirmed", "exit": str(e.id),
                                    "confirmed_by": by.member_code})
    return e


def deny_death(e: Exit, *, reason: str = "the member says they are alive") -> Exit:
    """The member is alive: the report is cancelled, on the record, and every member is
    told who made it."""
    with transaction.atomic():
        e = Exit.objects.select_for_update().get(pk=e.pk)
        if e.kind != Exit.Kind.DEATH or e.state != Exit.State.NOTICE:
            return e
        e.state = Exit.State.CANCELLED
        e.save(update_fields=["state"])
        ledger.append(chama=e.chama, type="membership", source="exit", member=e.membership,
                      rule_refs=[REF_EXIT],
                      decision={"change": "death_report_cancelled", "exit": str(e.id),
                                "reported_by": e.recorded_by.member_code, "why": reason})
    _tell_everyone(e.chama, {
        "en": f"JiraniChama: {e.chama.name}. {e.membership.member_code} was reported as "
              f"having died by {e.recorded_by.member_code}, but is alive. The report is "
              "cancelled and is on the record.",
        "sw": f"JiraniChama: {e.chama.name}. {e.membership.member_code} aliripotiwa "
              f"kufariki na {e.recorded_by.member_code}, lakini yu hai. Ripoti imefutwa na "
              "iko kwenye kumbukumbu.",
    })
    return e


def _tell_reported(e: Exit) -> None:
    from apps.notify.sms import SmsFailed, send_sms

    m = e.membership
    text = {
        "en": f"JiraniChama: {e.chama.name}. {e.recorded_by.member_code} has reported that "
              f"you have died. If this is not true, open JiraniChama and tap 'I am alive' "
              f"within {DEATH_OBJECTION_DAYS} days. Nothing happens to your money until then.",
        "sw": f"JiraniChama: {e.chama.name}. {e.recorded_by.member_code} ameripoti kwamba "
              "umefariki. Kama si kweli, fungua JiraniChama na ubonyeze 'Niko hai' ndani ya "
              f"siku {DEATH_OBJECTION_DAYS}. Hakuna kinachofanyika kwa pesa zako hadi hapo.",
    }
    try:
        send_sms(m.user.phone, text.get(m.user.language, text["en"]))
    except SmsFailed:
        pass


def _tell_everyone(chama: Chama, text: dict) -> None:
    from apps.notify.sms import SmsFailed, send_sms

    for m in Membership.objects.filter(chama=chama, state=Membership.State.ACTIVE) \
            .select_related("user"):
        try:
            send_sms(m.user.phone, text.get(m.user.language, text["en"]))
        except SmsFailed:
            pass


def _death_ready(e: Exit) -> bool:
    """A reported death is settled only once a second official has confirmed it and the
    member has not shown they are alive (they used the app after the report)."""
    user = e.membership.user
    if user.last_app_at is not None and user.last_app_at > e.created_at:
        deny_death(e, reason="the member used the app after the report")
        return False
    return e.confirmations.exists()


def _settle_inside(e: Exit) -> None:
    """Unpaid fines and the open loan are paid from the savings, inside the books."""
    from apps.loans import services as loans

    m = e.membership
    version = version_at(e.chama, timezone.localdate())
    for f in Fine.objects.filter(membership=m, state=Fine.State.RAISED):
        if f.outstanding_cents <= 0:
            continue
        ledger.append(chama=e.chama, type="fine_paid", source="exit", source_id=e.id,
                      member=m, amount_cents=f.outstanding_cents, period=f.period,
                      rule_version_id=version.id, rule_refs=[REF_EXIT, f.rule_ref],
                      decision={"fine_id": str(f.id), "from": "savings at exit"})
        f.paid_cents, f.state = f.amount_cents, Fine.State.PAID
        f.save(update_fields=["paid_cents", "state"])
    loan = loans.running_loan(m)
    if loan is None:
        return
    owed = loans.balance(loan)["outstanding_cents"]
    q = quote(m, death=e.kind == Exit.Kind.DEATH)
    available = max(q["savings_cents"] - q["deduction_cents"], 0)
    take = min(owed, available)
    if take > 0:
        ledger.append(chama=e.chama, type="loan_repayment", source="exit", source_id=e.id,
                      member=m, amount_cents=take, rule_version_id=version.id,
                      rule_refs=[REF_EXIT],
                      decision={"loan_id": str(loan.id), "from": "savings at exit"})
    if take < owed:
        loans._default(loan)  # the guarantors are called for the rest (5.8)
    else:
        transaction.on_commit(lambda: loans.settle(loan.pk))


def _propose(e: Exit, amount: int, purpose: str):
    from apps.payouts import services as payouts
    from apps.payouts.models import Proposal

    if e.kind == Exit.Kind.DEATH:
        return payouts.propose(e.chama, e.recorded_by, kind=Proposal.Kind.SETTLEMENT,
                               amount_cents=amount, purpose=purpose,
                               payee_name=e.next_of_kin_name,
                               payee_account=e.next_of_kin_phone, allow_arrears=True)
    return payouts.propose(e.chama, e.membership, kind=Proposal.Kind.SETTLEMENT,
                           amount_cents=amount, purpose=purpose, payee=e.membership,
                           allow_arrears=True)


def _finish(e: Exit) -> None:
    e.state = Exit.State.SETTLED
    e.save(update_fields=["state"])
    if e.kind == Exit.Kind.LEAVE:
        Membership.objects.filter(pk=e.membership_id).update(state=Membership.State.EXITED)
        ledger.append(chama=e.chama, type="membership", source="exit",
                      member=e.membership, rule_refs=[REF_EXIT],
                      decision={"change": "exited", "exit": str(e.id)})


def settle_due(chama: Chama | None = None) -> int:
    """Daily, and at once for a death: exits whose day has come are settled. A settlement
    the officials did not approve is proposed again the next day."""
    from apps.payouts.models import Proposal

    today = timezone.localdate()
    qs = Exit.objects.filter(state__in=[Exit.State.NOTICE, Exit.State.SETTLING],
                             exit_on__lte=today).select_related("chama", "membership__user")
    if chama is not None:
        qs = qs.filter(chama=chama)
    n = 0
    dead = (Proposal.State.REFUSED, Proposal.State.REJECTED, Proposal.State.EXPIRED,
            Proposal.State.CANCELLED)
    for e in qs:
        if e.kind == Exit.Kind.DEATH and e.state == Exit.State.NOTICE and not _death_ready(e):
            continue
        with transaction.atomic():
            e = Exit.objects.select_for_update().get(pk=e.pk)
            if e.state == Exit.State.CANCELLED:
                continue
            if e.state == Exit.State.NOTICE and e.kind == Exit.Kind.DEATH:
                Membership.objects.filter(pk=e.membership_id) \
                    .update(state=Membership.State.DECEASED)
                ledger.append(chama=e.chama, type="membership", source="exit",
                              member=e.membership, rule_refs=[REF_EXIT],
                              decision={"change": "deceased", "exit": str(e.id),
                                        "died_on": str(e.died_on)})
            if e.state == Exit.State.NOTICE:
                _settle_inside(e)
                now = quote(e.membership, death=e.kind == Exit.Kind.DEATH)
                # The deduction is the one shown on the day the member gave notice: a rule
                # changed during the notice period never applies to them.
                pct = e.quote.get("deduction_pct", now["deduction_pct"])
                now["deduction_pct"] = pct
                now["deduction_cents"] = now["savings_cents"] * pct // 100
                e.quote = {**now, "settled_from_savings": True, "before": e.quote}
                e.state = Exit.State.SETTLING
                e.save(update_fields=["quote", "state"])
            amount = e.quote["savings_cents"] - e.quote["deduction_cents"] \
                - e.quote["before"]["fines_cents"] - e.quote["before"]["loan_cents"]
            amount = max(amount, 0)
            try:
                if e.proposal_id is None or e.proposal.state in dead:
                    if amount <= 0:
                        _finish(e)
                        n += 1
                        continue
                    e.proposal = _propose(e, amount, "Exit settlement"
                                          if e.kind == Exit.Kind.LEAVE
                                          else f"Savings of {e.membership.member_code}")
                if e.kind == Exit.Kind.DEATH and e.quote["death_benefit_cents"] and (
                        e.benefit_proposal_id is None or e.benefit_proposal.state in dead):
                    e.benefit_proposal = _propose(e, e.quote["death_benefit_cents"],
                                                  "Welfare death benefit")
                e.save(update_fields=["proposal", "benefit_proposal"])
            except Problem:
                continue  # money out is off or refused; tried again tomorrow
        n += 1
    return n


def on_paid(p) -> None:
    """The settlement's debit matched (payouts._write_payout)."""
    e = Exit.objects.filter(proposal=p, state=Exit.State.SETTLING).first()
    if e is not None:
        _finish(e)
