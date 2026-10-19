"""Numbers for running the chama (Phase 5): how contributions, fines and meetings went
month by month, who is behind, and the money. Everything is worked out from the ledger and
the records every member can already see, so every member may read it too.
"""

from datetime import date

from django.db.models import Sum
from django.utils import timezone

from apps.chamas.models import Chama, Membership
from apps.contributions.projections import position
from apps.ledger.models import LedgerEntry
from apps.rules import calendar
from apps.rules.models import version_at


def _months(today: date, n: int) -> list[str]:
    out, p = [], calendar.period_of(today)
    for _ in range(n):
        out.append(p)
        y, m = map(int, p.split("-"))
        p = f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"
    return list(reversed(out))


def _sum(qs) -> int:
    return qs.aggregate(t=Sum("amount_cents"))["t"] or 0


def report(chama: Chama, *, months: int = 6, today: date | None = None) -> dict:
    today = today or timezone.localdate()
    members = list(Membership.objects.filter(
        chama=chama, state__in=[Membership.State.ACTIVE, Membership.State.PROBATION])
        .select_related("user"))
    entries = LedgerEntry.objects.filter(chama=chama)
    now_version = version_at(chama, today)
    rows = []
    for p in _months(today, months):
        version = (version_at(chama, calendar.due_date(p, now_version.rules)) or now_version
                   if now_version else None)
        amount = version.rules["contributions"]["amount_cents"] if version else 0
        expected = (sum(1 for m in members if m.joined_on
                        and calendar.first_period(m.joined_on, version.rules) <= p)
                    if version else 0)
        contrib = entries.filter(type="contribution", period=p)
        due = expected * amount
        paid = _sum(contrib)
        rows.append({
            "period": p,
            "due_cents": due,
            "paid_cents": paid,
            "paid_pct": round(100 * paid / due) if due else None,
            "on_time_cents": _sum(contrib.filter(decision__outcome="on_time")),
            "late_cents": _sum(contrib.filter(decision__outcome="late")),
            "fines_raised_cents": _sum(entries.filter(type="fine_raised", period=p)),
            "fines_paid_cents": _sum(entries.filter(type="fine_paid", period=p)),
            "fines_waived_cents": _sum(entries.filter(type="fine_waived", period=p)),
        })

    behind = []
    for m in members:
        pos = position(m, today)
        if pos.arrears_cents or pos.fines_cents:
            behind.append({"member_code": m.member_code,
                           "name": m.user.display_name or m.member_code,
                           "arrears_cents": pos.arrears_cents, "fines_cents": pos.fines_cents})
    behind.sort(key=lambda b: -(b["arrears_cents"] + b["fines_cents"]))

    meetings = []
    for e in entries.filter(type="meeting_closed").order_by("-seq")[:months]:
        d = e.decision
        came = len(d.get("present", [])) + len(d.get("late", []))
        total = came + len(d.get("excused", [])) + len(d.get("absent", []))
        meetings.append({"title": d.get("title"), "starts_at": d.get("starts_at"),
                         "attended": came, "expected": total,
                         "attendance_pct": round(100 * came / total) if total else None})

    from apps.investments.services import portfolio
    from apps.loans.models import Loan
    from apps.loans.services import balance, enabled

    loans = None
    if enabled(chama) is not None:
        running = list(Loan.objects.filter(chama=chama, state__in=Loan.RUNNING))
        loans = {"running": len(running),
                 "outstanding_cents": sum(balance(x)["outstanding_cents"] for x in running),
                 "in_arrears": sum(1 for x in running if x.state == Loan.State.IN_ARREARS),
                 "defaulted": sum(1 for x in running if x.state == Loan.State.DEFAULTED)}

    folio = portfolio(chama)
    return {
        "months": rows,
        "members": len(members),
        "behind": behind[:20],
        "behind_count": len(behind),
        "meetings": list(reversed(meetings)),
        "loans": loans,
        "money": {"contributed_cents": _sum(entries.filter(type="contribution")),
                  "bank_cents": folio["bank_cents"],
                  "investments_cents": folio["value_cents"],
                  "net_worth_cents": folio["net_worth_cents"]},
    }
