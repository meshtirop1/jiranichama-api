"""Judging a payment against the rules (5.3 step 6). Pure functions, no database.

The same code runs live, in golden tests, and (Phase 3) in the rule-change preview.
"""

from dataclasses import dataclass, field
from datetime import date

from . import calendar

REF_CONTRIBUTION = "3.1"
REF_LATE_FINE = "3.2"
REF_ORDER = "3.3"


@dataclass(frozen=True)
class Due:
    period: str
    outstanding_cents: int


@dataclass(frozen=True)
class OpenFine:
    id: str
    period: str
    outstanding_cents: int


@dataclass
class Slice:
    kind: str  # "contribution" or "fine"
    amount_cents: int
    period: str | None
    outcome: str  # on_time, late, advance, credit, fine_paid
    rule_refs: list[str]
    fine_id: str | None = None

    def as_dict(self) -> dict:
        return {"kind": self.kind, "amount_cents": self.amount_cents, "period": self.period,
                "outcome": self.outcome, "rule_refs": self.rule_refs, "fine_id": self.fine_id}


@dataclass
class Allocation:
    slices: list[Slice] = field(default_factory=list)
    # Late fines raised before this payment reached us, although it was made in time.
    fines_to_reverse: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"slices": [s.as_dict() for s in self.slices],
                "fines_to_reverse": self.fines_to_reverse}


def late_fine_cents(rules: dict) -> int:
    fine = rules["fines"]["late_contribution"]
    if not fine["enabled"]:
        return 0
    cap = fine["monthly_cap_cents"]
    return fine["amount_cents"] if cap is None else min(fine["amount_cents"], cap)


def _contribution_slice(rules, period, amount, value_date, current) -> Slice:
    if period > current:
        outcome = "advance"
    elif value_date <= calendar.grace_end(period, rules):
        outcome = "on_time"
    else:
        outcome = "late"
    return Slice("contribution", amount, period, outcome, [REF_CONTRIBUTION, REF_ORDER])


def allocate(*, rules: dict, amount_cents: int, value_date: date, dues: list[Due],
             fines: list[OpenFine]) -> Allocation:
    """Split one payment across arrears, fines and the current period in the order the
    rules set, then into future periods (advance), then any rest as a credit.

    ``dues`` lists every period the member owes for, from their first period to some
    months ahead, with what is still outstanding. ``fines`` lists unpaid fines.
    A fine for a period whose grace ended *after* this payment's date cannot be paid by
    it: the payment was made before the fine was due. If the payment then completes that
    period on time, the fine was raised in error and is reversed.
    """
    if amount_cents <= 0:
        return Allocation()
    current = calendar.period_of(value_date)
    remaining = amount_cents
    out = Allocation()
    owed = {d.period: d.outstanding_cents for d in dues}
    allocated: dict[str, int] = {}

    def pay_period(period: str) -> None:
        nonlocal remaining
        take = min(remaining, owed.get(period, 0) - allocated.get(period, 0))
        if take > 0:
            out.slices.append(_contribution_slice(rules, period, take, value_date, current))
            allocated[period] = allocated.get(period, 0) + take
            remaining -= take

    payable_fines = [f for f in fines if calendar.grace_end(f.period, rules) < value_date]
    in_error = [f for f in fines if calendar.grace_end(f.period, rules) >= value_date]

    for step in rules["contributions"]["allocation_order"]:
        if remaining <= 0:
            break
        if step == "arrears":
            for d in sorted(dues, key=lambda d: d.period):
                if d.period < current:
                    pay_period(d.period)
        elif step == "current":
            pay_period(current)
        elif step == "fines":
            for f in sorted(payable_fines, key=lambda f: (f.period, f.id)):
                take = min(remaining, f.outstanding_cents)
                if take > 0:
                    out.slices.append(Slice("fine", take, f.period, "fine_paid",
                                            [REF_LATE_FINE, REF_ORDER], fine_id=f.id))
                    remaining -= take

    for d in sorted(dues, key=lambda d: d.period):
        if remaining <= 0:
            break
        if d.period > current:
            pay_period(d.period)

    if remaining > 0:
        out.slices.append(Slice("contribution", remaining, None, "credit", [REF_CONTRIBUTION]))

    for f in in_error:
        period_slices = [s for s in out.slices if s.kind == "contribution"
                         and s.period == f.period]
        fully_paid = owed.get(f.period, 0) - allocated.get(f.period, 0) == 0
        if period_slices and fully_paid and all(s.outcome == "on_time" for s in period_slices):
            out.fines_to_reverse.append(f.id)
    return out
