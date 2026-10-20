"""Opening balances (5.1 step 10) and cash at meetings (5.4)."""

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
from apps.feeds.models import BankTransaction
from apps.fines.models import Fine
from apps.ledger import services as ledger
from apps.reconciliation.models import Match
from apps.reconciliation.services import apply_payment
from apps.rules import calendar, engine
from apps.rules.models import version_at

from .models import CashReceipt, OpeningBalance, OpeningBalanceLine, OpeningObjection

OBJECTION_DAYS = 14
OPENING_FINE_REF = "opening"
CONFIRMERS = OFFICIAL_ROLES + ("auditor",)


def _sha(obj) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def _require_active(chama: Chama) -> None:
    if chama.state != Chama.State.ACTIVE:
        raise Problem("chama_not_active", "This is done once the chama is active.",
                      status.HTTP_409_CONFLICT)


def _require_treasurer(by: Membership) -> None:
    if by.role != Membership.Role.TREASURER:
        raise Problem("not_allowed", "Only the treasurer does this.", status.HTTP_403_FORBIDDEN)


# --- Opening balances -------------------------------------------------------------------

@transaction.atomic
def save_opening(chama: Chama, by: Membership, lines: list[dict]) -> OpeningBalance:
    """Draft (or redraft) each member's opening savings and unpaid fines."""
    _require_active(chama)
    _require_treasurer(by)
    ob, _ = OpeningBalance.objects.select_for_update().get_or_create(
        chama=chama, defaults={"proposed_by": by.user})
    if ob.state == OpeningBalance.State.CONFIRMED:
        raise Problem("opening_confirmed", "Opening balances are already confirmed.",
                      status.HTTP_409_CONFLICT)
    members = {str(m.id): m for m in chama.memberships.all()}
    seen = set()
    for line in lines:
        mid = str(line.get("membership_id"))
        if mid not in members or mid in seen:
            raise Problem("invalid_member", "Each line must be a different member of this chama.")
        seen.add(mid)
        for key in ("savings_cents", "fines_cents"):
            value = line.get(key, 0)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise Problem("invalid_amount", "Amounts are whole cents, zero or more.")
    ob.lines.all().delete()  # draft lines only; nothing is on the ledger yet
    for line in lines:
        OpeningBalanceLine.objects.create(
            opening=ob, membership=members[str(line["membership_id"])],
            savings_cents=line.get("savings_cents", 0), fines_cents=line.get("fines_cents", 0))
    ob.proposed_by = by.user
    ob.save(update_fields=["proposed_by", "updated_at"])
    return ob


def _opening_hash(ob: OpeningBalance) -> str:
    return _sha({"chama": str(ob.chama_id), "lines": sorted(
        [str(x.membership_id), x.savings_cents, x.fines_cents] for x in ob.lines.all())})


def _require_opening_confirmer(ob: OpeningBalance, by: Membership) -> None:
    if ob.state != OpeningBalance.State.DRAFT or not ob.lines.exists():
        raise Problem("nothing_to_confirm", "There are no draft opening balances to confirm.",
                      status.HTTP_409_CONFLICT)
    if by.role not in CONFIRMERS:
        raise Problem("not_allowed", "Only an official confirms opening balances.",
                      status.HTTP_403_FORBIDDEN)
    if by.user_id == ob.proposed_by_id:
        raise Problem("second_official_required", "A different official must confirm the "
                                                  "opening balances you entered.",
                      status.HTTP_403_FORBIDDEN)


def start_confirm_opening(ob: OpeningBalance, by: Membership) -> SigningChallenge:
    _require_opening_confirmer(ob, by)
    total = sum(x.savings_cents for x in ob.lines.all())
    action = (f"CONFIRM opening balances for {ob.lines.count()} members, savings "
              f"{money.fmt(total, ob.chama)}") if by.user.language != "sw" else (
        f"KUTHIBITISHA salio la mwanzo la wanachama {ob.lines.count()}, akiba "
        f"{money.fmt(total, ob.chama)}")
    return signing.issue(user=by.user, purpose=SigningChallenge.Purpose.CONFIRM_OPENING,
                         subject=f"contributions.opening:{ob.chama_id}",
                         content_hash=_opening_hash(ob), action_text=action)


def confirm_opening(ob: OpeningBalance, by: Membership, *, challenge_id, sms_code: str
                    ) -> OpeningBalance:
    _require_opening_confirmer(ob, by)
    signing.confirm(user=by.user, challenge_id=challenge_id, code=sms_code,
                    purpose=SigningChallenge.Purpose.CONFIRM_OPENING,
                    subject=f"contributions.opening:{ob.chama_id}",
                    content_hash=_opening_hash(ob))
    with transaction.atomic():
        ob = OpeningBalance.objects.select_for_update().get(pk=ob.pk)
        _require_opening_confirmer(ob, by)
        proposer = ob.chama.memberships.filter(user=ob.proposed_by).first()
        signed_by = [proposer.member_code if proposer else None, by.member_code]
        period = calendar.period_of(timezone.localdate())
        for line in ob.lines.select_related("membership").order_by("membership__member_code"):
            if line.savings_cents:
                line.savings_entry = ledger.append(
                    chama=ob.chama, type="opening_balance", source="proposal",
                    source_id=line.id, member=line.membership, amount_cents=line.savings_cents,
                    decision={"what": "savings", "signed_by": signed_by})
            if line.fines_cents:
                line.fines_entry = ledger.append(
                    chama=ob.chama, type="fine_raised", source="proposal", source_id=line.id,
                    member=line.membership, amount_cents=line.fines_cents, period=period,
                    rule_refs=[OPENING_FINE_REF],
                    decision={"what": "unpaid fines brought in", "signed_by": signed_by})
                Fine.objects.create(chama=ob.chama, membership=line.membership,
                                    rule_ref=OPENING_FINE_REF, period=period,
                                    amount_cents=line.fines_cents,
                                    raised_entry=line.fines_entry)
            line.save(update_fields=["savings_entry", "fines_entry"])
        ob.state = OpeningBalance.State.CONFIRMED
        ob.confirmed_by = by.user
        ob.confirmed_at = timezone.now()
        ob.objection_until = ob.confirmed_at + timedelta(days=OBJECTION_DAYS)
        ob.save(update_fields=["state", "confirmed_by", "confirmed_at", "objection_until",
                               "updated_at"])
    return ob


def object_to_opening(line: OpeningBalanceLine, by: Membership, reason: str
                      ) -> OpeningObjection:
    ob = line.opening
    if by.id != line.membership_id:
        raise Problem("not_yours", "You can object only to your own opening balance.",
                      status.HTTP_403_FORBIDDEN)
    if ob.state != OpeningBalance.State.CONFIRMED or timezone.now() > ob.objection_until:
        raise Problem("objection_closed", "The time to object has passed.",
                      status.HTTP_409_CONFLICT)
    if not (reason or "").strip():
        raise Problem("reason_required", "Say what you think is wrong.")
    obj, created = OpeningObjection.objects.get_or_create(
        line=line, raised_by=by.user, defaults={"reason": reason.strip()})
    if not created:
        raise Problem("already_objected", "You have already objected.", status.HTTP_409_CONFLICT)
    return obj


# --- Cash at meetings -------------------------------------------------------------------

def record_cash(chama: Chama, by: Membership, member: Membership, *, amount_cents: int,
                received_on) -> CashReceipt:
    _require_active(chama)
    _require_treasurer(by)
    if member.chama_id != chama.id:
        raise Problem("invalid_member", "That member is not in this chama.")
    if amount_cents <= 0:
        raise Problem("invalid_amount", "Enter the amount received.")
    if received_on > timezone.localdate():
        raise Problem("future_date", "The date cannot be in the future.")
    return CashReceipt.objects.create(chama=chama, membership=member, amount_cents=amount_cents,
                                      received_on=received_on, recorded_by=by.user)


def _cash_hash(r: CashReceipt) -> str:
    return _sha({"receipt": str(r.id), "amount_cents": r.amount_cents,
                 "received_on": r.received_on.isoformat()})


def _require_own_unconfirmed(r: CashReceipt, by: Membership) -> None:
    if by.id != r.membership_id:
        raise Problem("not_yours", "Only the member who paid confirms the cash.",
                      status.HTTP_403_FORBIDDEN)
    if r.state != CashReceipt.State.RECORDED:
        raise Problem("not_awaiting_member", "This cash is not waiting for you.",
                      status.HTTP_409_CONFLICT)


def start_confirm_cash(r: CashReceipt, by: Membership) -> SigningChallenge:
    _require_own_unconfirmed(r, by)
    amount = money.fmt(r.amount_cents, r.membership)
    action = (f"CONFIRM you gave {amount} cash on {r.received_on}" if by.user.language != "sw"
              else f"KUTHIBITISHA ulitoa {amount} taslimu tarehe {r.received_on}")
    return signing.issue(user=by.user, purpose=SigningChallenge.Purpose.CONFIRM_CASH,
                         subject=f"contributions.cash:{r.id}", content_hash=_cash_hash(r),
                         action_text=action)


def confirm_cash(r: CashReceipt, by: Membership, *, challenge_id, sms_code: str) -> CashReceipt:
    _require_own_unconfirmed(r, by)
    signing.confirm(user=by.user, challenge_id=challenge_id, code=sms_code,
                    purpose=SigningChallenge.Purpose.CONFIRM_CASH,
                    subject=f"contributions.cash:{r.id}", content_hash=_cash_hash(r))
    with transaction.atomic():
        r = CashReceipt.objects.select_for_update().get(pk=r.pk)
        _require_own_unconfirmed(r, by)
        r.pending_entry = ledger.append(
            chama=r.chama, type="contribution_pending_cash", source="proposal", source_id=r.id,
            member=r.membership, amount_cents=r.amount_cents,
            decision={"received_on": r.received_on.isoformat(),
                      "note": "cash, waiting for bank deposit"})
        r.state = CashReceipt.State.MEMBER_CONFIRMED
        r.member_confirmed_at = timezone.now()
        r.save(update_fields=["pending_entry", "state", "member_confirmed_at"])
    return r


def decline_cash(r: CashReceipt, by: Membership, reason: str = "") -> CashReceipt:
    """The member says they did not hand over this cash. Nothing counts; officials see it."""
    _require_own_unconfirmed(r, by)
    r.state = CashReceipt.State.DECLINED
    r.declined_reason = (reason or "").strip()
    r.save(update_fields=["state", "declined_reason"])
    return r


def is_overdue(r: CashReceipt, today=None) -> bool:
    if r.state != CashReceipt.State.MEMBER_CONFIRMED:
        return False
    version = version_at(r.chama, r.received_on)
    days = engine.cash_banking_days(version.rules) if version else 3
    return (today or timezone.localdate()) > r.received_on + timedelta(days=days)


@transaction.atomic
def bank_cash(txn: BankTransaction, by: Membership, receipt_ids: list) -> list:
    """Link a bank deposit to the confirmed cash receipts it carries (5.4 step 4). Each
    member's share is judged by the rules: from the day the cash was handed over if it
    reached the bank in time, otherwise from the bank date."""
    _require_treasurer(by)
    txn = BankTransaction.objects.select_for_update().get(pk=txn.pk)
    if txn.chama_id != by.chama_id or txn.direction != BankTransaction.Direction.CREDIT or \
            txn.state != BankTransaction.State.UNMATCHED:
        raise Problem("not_unmatched", "Only an unmatched deposit can be linked to cash.",
                      status.HTTP_409_CONFLICT)
    receipts = list(CashReceipt.objects.select_for_update().filter(
        pk__in=receipt_ids, chama_id=txn.chama_id, state=CashReceipt.State.MEMBER_CONFIRMED))
    if not receipts or len(receipts) != len(set(map(str, receipt_ids))):
        raise Problem("invalid_receipts", "Choose cash receipts the members have confirmed.")
    if sum(r.amount_cents for r in receipts) != txn.amount_cents:
        raise Problem("amounts_differ", "The cash receipts must add up to the deposit.",
                      deposit_cents=txn.amount_cents,
                      receipts_cents=sum(r.amount_cents for r in receipts))
    match = Match.objects.create(bank_txn=txn, method=Match.Method.CASH, decided_by=by.user,
                                 reason=f"{len(receipts)} cash receipts")
    entries = []
    for r in sorted(receipts, key=lambda r: (r.received_on, str(r.id))):
        version = version_at(txn.chama, r.received_on)
        if version is None:
            raise Problem("no_rules_on_date", "The chama's rules did not exist on that date.",
                          status.HTTP_409_CONFLICT)
        in_time = txn.value_date <= r.received_on + timedelta(
            days=engine.cash_banking_days(version.rules))
        counts_from = r.received_on if in_time else txn.value_date
        base = {"bank_ref": txn.external_ref, "value_date": counts_from.isoformat(),
                "method": Match.Method.CASH, "match_id": match.id,
                "cash_receipt": str(r.id), "banked_in_time": in_time}
        entries += apply_payment(txn=txn, member=r.membership, amount_cents=r.amount_cents,
                                 value_date=counts_from, version=version, base=base)
        r.banked_entry = ledger.append(
            chama=txn.chama, type="cash_banked", source="bank_txn", source_id=txn.id,
            member=r.membership, amount_cents=0,
            decision={"cash_receipt": str(r.id), "pending_entry": str(r.pending_entry_id),
                      "banked_in_time": in_time})
        r.state, r.bank_txn = CashReceipt.State.BANKED, txn
        r.save(update_fields=["banked_entry", "state", "bank_txn"])
    txn.state = BankTransaction.State.RECORDED
    txn.save(update_fields=["state"])
    return entries
