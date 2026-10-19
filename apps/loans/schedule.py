"""Loan terms and repayment schedules from the rules (5.8). Pure functions, no database.

Monthly instalments on the same day of the month as the disbursement (the 28th at most),
equal principal each month (the last one takes the rounding), and interest either flat on
the amount borrowed or reducing on what is still owed. Rates are basis points a month
(100 = 1% a month). Amounts are whole cents, rounded half up.
"""

from dataclasses import dataclass
from datetime import date

DEFAULTS = {"min_months": 3, "savings_multiple_pct": 300, "interest": "flat",
            "monthly_rate_bp": 100, "max_term_months": 12, "guarantors": 2, "grace_days": 3,
            "late_penalty_cents": 0, "default_after_missed": 2, "contribution_first": True}
REF_ELIGIBILITY, REF_INTEREST, REF_PENALTY, REF_DEFAULT = "12.1", "12.2", "12.3", "12.4"


def settings_from(rules: dict) -> dict | None:
    """The chama's loan rules, or None when its rules have no loans section."""
    if not rules.get("loans"):
        return None
    return {**DEFAULTS, **rules["loans"]}


@dataclass(frozen=True)
class Instalment:
    no: int
    due: date
    principal_cents: int
    interest_cents: int

    @property
    def total_cents(self) -> int:
        return self.principal_cents + self.interest_cents

    def as_dict(self) -> dict:
        return {"no": self.no, "due": self.due.isoformat(),
                "principal_cents": self.principal_cents,
                "interest_cents": self.interest_cents, "total_cents": self.total_cents}


def _round(x: float) -> int:
    return int(x + 0.5)


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, min(d.day, 28))


def build(*, amount_cents: int, term_months: int, s: dict, start: date) -> list[Instalment]:
    base = amount_cents // term_months
    out, owed = [], amount_cents
    for i in range(1, term_months + 1):
        principal = base if i < term_months else owed
        on = amount_cents if s["interest"] == "flat" else owed
        interest = _round(on * s["monthly_rate_bp"] / 10_000)
        out.append(Instalment(i, _add_months(start, i), principal, interest))
        owed -= principal
    return out


def from_dicts(rows: list[dict]) -> list[Instalment]:
    return [Instalment(r["no"], date.fromisoformat(r["due"]), r["principal_cents"],
                       r["interest_cents"]) for r in rows]
