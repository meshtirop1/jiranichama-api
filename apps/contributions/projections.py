"""What a member owes and has paid, worked out from the ledger every time.

Balances are projections of ledger entries, never stored numbers (6.1 principle 1).
"""

from dataclasses import dataclass
from datetime import date

from django.db.models import Sum

from apps.chamas.models import Membership
from apps.fines.models import Fine
from apps.ledger.models import LedgerEntry
from apps.rules import calendar, engine

ADVANCE_MONTHS = 12

# Entries that move a member's contribution total for a period.
# A correction decided in a dispute (5.14) puts a member's savings right.
_CONTRIBUTION_TYPES = ("contribution", "correction")


def paid_by_period(membership: Membership) -> dict[str, int]:
    """Contributions per period, net of reversals of those contributions."""
    rows = (LedgerEntry.objects.filter(member=membership, type__in=_CONTRIBUTION_TYPES)
            .values("period").annotate(total=Sum("amount_cents")))
    paid = {r["period"]: r["total"] for r in rows}
    reversed_rows = (LedgerEntry.objects.filter(member=membership, type="reversal",
                                                reverses__type__in=_CONTRIBUTION_TYPES)
                     .values("period").annotate(total=Sum("amount_cents")))
    for r in reversed_rows:
        paid[r["period"]] = paid.get(r["period"], 0) + r["total"]
    return paid


def member_first_period(membership: Membership, rules: dict) -> str | None:
    if membership.joined_on is None:
        return None
    return calendar.first_period(membership.joined_on, rules)


def rule_timeline(chama) -> list:
    """Every rule version that has taken effect, newest first: one query, then each
    month's rules are picked in memory (was one query per month)."""
    from apps.rules.models import RuleVersion

    return list(RuleVersion.objects.filter(chama=chama)
                .exclude(state=RuleVersion.State.SCHEDULED)
                .order_by("-effective_from", "-version_no"))


def _version_on(timeline: list, on: date):
    for v in timeline:
        if v.effective_from <= on:
            return v
    return None


def dues(membership: Membership, on: date, *, timeline: list | None = None,
         paid: dict | None = None) -> list[engine.Due]:
    """Every period from the member's first one to ADVANCE_MONTHS past ``on``, with what
    is still outstanding under the rules in force at each period's due date."""
    chama = membership.chama
    if timeline is None:
        timeline = rule_timeline(chama)
    rules_now = _version_on(timeline, on)
    if rules_now is None:
        return []
    first = member_first_period(membership, rules_now.rules)
    if first is None:
        return []
    last = calendar.period_of(on)
    for _ in range(ADVANCE_MONTHS):
        last = calendar.next_period(last)
    if paid is None:
        paid = paid_by_period(membership)
    out = []
    for p in calendar.periods(first, last):
        version = _version_on(timeline, calendar.due_date(p, rules_now.rules)) or rules_now
        amount = version.rules["contributions"]["amount_cents"]
        out.append(engine.Due(p, max(amount - paid.get(p, 0), 0)))
    return out


def open_fines(membership: Membership) -> list[engine.OpenFine]:
    return [engine.OpenFine(str(f.id), f.period, f.outstanding_cents,
                            late=f.rule_ref == engine.REF_LATE_FINE)
            for f in Fine.objects.filter(membership=membership, state=Fine.State.RAISED)
            if f.outstanding_cents > 0]


def shared_out_cents(membership: Membership) -> int:
    """Savings already given back in a share-out (on the payout entry, or on the
    ``shareout`` entry when the bank paid nothing). They are no longer the member's
    savings, so they are never paid twice (exit, the next share-out)."""
    total = 0
    for d in (LedgerEntry.objects.filter(member=membership, type__in=("payout", "shareout"),
                                         decision__has_key="savings_out_cents")
              .values_list("decision", flat=True)):
        total += int(d.get("savings_out_cents") or 0)
    return total


@dataclass
class Position:
    contributed_cents: int
    opening_cents: int
    pending_cash_cents: int
    arrears_cents: int
    fines_cents: int
    credit_cents: int
    next_due_period: str | None
    next_due_cents: int

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def position(membership: Membership, on: date, *, timeline: list | None = None) -> Position:
    paid = paid_by_period(membership)
    owed = dues(membership, on, timeline=timeline, paid=paid)
    current = calendar.period_of(on)
    arrears = sum(d.outstanding_cents for d in owed if d.period < current)
    upcoming = [d for d in owed if d.period >= current and d.outstanding_cents > 0]
    from .models import CashReceipt

    opening = (LedgerEntry.objects.filter(member=membership, type="opening_balance")
               .aggregate(t=Sum("amount_cents"))["t"] or 0)
    pending = (CashReceipt.objects.filter(membership=membership,
                                          state=CashReceipt.State.MEMBER_CONFIRMED)
               .aggregate(t=Sum("amount_cents"))["t"] or 0)
    return Position(
        contributed_cents=sum(v for k, v in paid.items() if k is not None) + opening
        - shared_out_cents(membership),
        opening_cents=opening,
        pending_cash_cents=pending,
        arrears_cents=arrears,
        fines_cents=sum(f.outstanding_cents for f in open_fines(membership)),
        credit_cents=paid.get(None, 0),
        next_due_period=upcoming[0].period if upcoming else None,
        next_due_cents=upcoming[0].outstanding_cents if upcoming else 0,
    )
