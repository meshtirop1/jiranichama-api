"""Monthly contribution periods ("2026-03"), due dates and grace ends."""

from datetime import date, timedelta


def period_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def parse_period(p: str) -> tuple[int, int]:
    year, month = p.split("-")
    return int(year), int(month)


def next_period(p: str) -> str:
    year, month = parse_period(p)
    return f"{year + (month == 12):04d}-{month % 12 + 1:02d}"


def due_date(p: str, rules: dict) -> date:
    year, month = parse_period(p)
    return date(year, month, rules["contributions"]["due_day"])


def grace_end(p: str, rules: dict) -> date:
    """The last day a payment for period ``p`` is on time."""
    return due_date(p, rules) + timedelta(days=rules["contributions"]["grace_days"])


def first_period(start: date, rules: dict) -> str:
    """The first period someone who starts on ``start`` owes: this month if they start
    on or before the due day, otherwise next month."""
    p = period_of(start)
    return p if start.day <= rules["contributions"]["due_day"] else next_period(p)


def periods(first: str, last: str) -> list[str]:
    out, p = [], first
    while p <= last:
        out.append(p)
        p = next_period(p)
    return out
