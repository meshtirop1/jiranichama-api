"""The figures on a member statement and a chama statement.

Everything here is worked out from the ledger, never from stored balances (6.1). The
result is plain JSON with no words in it: the same figures render in English or
Kiswahili, and their canonical JSON is the statement's fingerprint.
"""

from datetime import date, timedelta

from django.db.models import Count, Max, Min, Sum
from django.utils import timezone

from apps.chamas.models import Chama, Membership
from apps.contributions.models import CashReceipt
from apps.contributions.projections import position
from apps.feeds.models import BankTransaction, Statement
from apps.ledger.models import LedgerEntry

DATA_VERSION = 1

# Entries a member statement lists. Governance entries (rules adopted, roles, devices)
# and seals are on the chama's record, not on anyone's money statement.
MEMBER_TYPES = (
    "opening_balance", "contribution", "contribution_pending_cash", "fine_raised",
    "fine_waived", "fine_paid", "payout", "loan_disbursed", "reversal", "correction",
)


def _day(entry: LedgerEntry) -> date:
    """The day the money moved: the bank's value date when we have one, the day a late
    fine applied from, else the day the entry was written."""
    decision = entry.decision or {}
    if decision.get("value_date"):
        return date.fromisoformat(decision["value_date"])
    if entry.type == "fine_raised" and decision.get("grace_end"):
        # A late fine applies from the day after grace, even if the run that raised it
        # (or the statement that showed the payment missing) came later.
        return date.fromisoformat(decision["grace_end"]) + timedelta(days=1)
    return timezone.localdate(entry.at)


def records_to_seq(chama: Chama) -> int:
    """The last entry the figures take into account. Seals are left out, so issuing a
    statement does not by itself change the next statement's figures."""
    return (LedgerEntry.objects.filter(chama=chama).exclude(type="seal")
            .aggregate(s=Max("seq"))["s"] or 0)


def _name(m: Membership) -> str:
    return m.user.display_name if m.user_id else ""


def default_start(chama: Chama, membership: Membership | None = None) -> date:
    first = (LedgerEntry.objects.filter(chama=chama).order_by("seq")
             .values_list("at", flat=True).first())
    candidates = [timezone.localdate(first)] if first else []
    if membership is not None:
        joined = membership.joined_on
    else:
        joined = (Membership.objects.filter(chama=chama, joined_on__isnull=False)
                  .aggregate(j=Min("joined_on"))["j"])
    if joined:
        candidates.append(joined)
    if chama.activated_at:
        candidates.append(timezone.localdate(chama.activated_at))
    return min(candidates) if candidates else timezone.localdate()


def _member_lines(membership: Membership, start: date, end: date) -> tuple[list, dict]:
    entries = list(LedgerEntry.objects.filter(member=membership, type__in=MEMBER_TYPES)
                   .select_related("reverses").order_by("seq"))
    reversed_ids = {e.reverses_id for e in entries if e.reverses_id}
    lines, totals = [], {"opening_cents": 0, "contributed_cents": 0, "fines_raised_cents": 0,
                         "fines_paid_cents": 0, "fines_waived_cents": 0}
    for e in entries:
        day = _day(e)
        if not start <= day <= end:
            continue
        lines.append({
            "date": day.isoformat(),
            "seq": e.seq,
            "receipt": e.receipt_code,
            "type": e.type,
            "period": e.period,
            "amount_cents": e.amount_cents,
            "reversed": e.id in reversed_ids,
            "reverses_receipt": e.reverses.receipt_code if e.reverses_id else None,
            "reverses_type": e.reverses.type if e.reverses_id else None,
        })
        kind = e.reverses.type if e.type == "reversal" and e.reverses_id else e.type
        key = {"opening_balance": "opening_cents", "contribution": "contributed_cents",
               "fine_raised": "fines_raised_cents", "fine_paid": "fines_paid_cents",
               "fine_waived": "fines_waived_cents"}.get(kind)
        if key:
            totals[key] += e.amount_cents
    lines.sort(key=lambda line: (line["date"], line["seq"]))
    return lines, totals


def member_statement(membership: Membership, start: date, end: date) -> dict:
    lines, totals = _member_lines(membership, start, end)
    today = timezone.localdate()
    return {
        "v": DATA_VERSION,
        "kind": "member_statement",
        "chama": {"id": str(membership.chama_id), "name": membership.chama.name},
        "member": {"code": membership.member_code, "name": _name(membership),
                   "role": membership.role},
        "from": start.isoformat(),
        "to": end.isoformat(),
        "lines": lines,
        "totals": totals,
        "position": {"on": today.isoformat(), **position(membership, today).as_dict()},
        "records_to_seq": records_to_seq(membership.chama),
    }


def chama_statement(chama: Chama, start: date, end: date) -> dict:
    today = timezone.localdate()
    members = (Membership.objects.filter(chama=chama).exclude(state=Membership.State.EXITED)
               .select_related("user").order_by("member_code"))
    rows, sums = [], {"contributed_cents": 0, "savings_cents": 0, "arrears_cents": 0,
                      "fines_cents": 0, "pending_cash_cents": 0}
    for m in members:
        if not m.is_current:
            continue
        in_range = _member_lines(m, start, end)[1]["contributed_cents"]
        p = position(m, today)
        row = {"code": m.member_code, "name": _name(m), "role": m.role,
               "contributed_cents": in_range, "savings_cents": p.contributed_cents,
               "arrears_cents": p.arrears_cents, "fines_cents": p.fines_cents,
               "pending_cash_cents": p.pending_cash_cents}
        rows.append(row)
        for k in sums:
            sums[k] += row[k]

    last = (Statement.objects.filter(chama=chama, state=Statement.State.ACCEPTED)
            .order_by("-period_to").first())
    unmatched = BankTransaction.objects.filter(
        chama=chama, state=BankTransaction.State.UNMATCHED,
        direction=BankTransaction.Direction.CREDIT)
    agg = unmatched.aggregate(n=Count("id"), t=Sum("amount_cents"))
    cash_waiting = (CashReceipt.objects.filter(chama=chama,
                                               state=CashReceipt.State.MEMBER_CONFIRMED)
                    .aggregate(t=Sum("amount_cents"))["t"] or 0)
    return {
        "v": DATA_VERSION,
        "kind": "chama_statement",
        "chama": {"id": str(chama.id), "name": chama.name},
        "from": start.isoformat(),
        "to": end.isoformat(),
        "members": rows,
        "totals": sums,
        "bank": {
            "reconciled_to": last.period_to.isoformat() if last else None,
            "closing_cents": last.closing_cents if last else None,
            "unmatched_count": agg["n"] or 0,
            "unmatched_cents": agg["t"] or 0,
            "cash_waiting_cents": cash_waiting,
        },
        "on": today.isoformat(),
        "records_to_seq": records_to_seq(chama),
    }
