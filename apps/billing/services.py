"""The monthly bill (owner decisions 2026-10-20; JiraniChama_Countries_Plan section 5).

- Price: about USD 0.10 per active member per month, set in local money and rounded
  (KES 15, UGX 400, TZS 300; other currencies from the rough KES rate).
- Active: a member with at least one record in the chama that month. Members who left or
  did nothing that month are free.
- Free: the first three months after the chama goes active, and any month with five
  active members or fewer.
- ID checks the chama paid for (rule 6.1) are added at their price.
"""

from datetime import date, timedelta
from math import floor, log10

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.chamas.models import Chama
from apps.core import countries

from .models import Bill

PRICE_USD = 0.10
FREE_MONTHS = 3
FREE_UP_TO_MEMBERS = 5
# Rounded local prices for the open countries (about USD 0.10).
LOCAL_PRICE_CENTS = {"KES": 1_500, "UGX": 40_000, "TZS": 30_000}


def price_per_member_cents(currency: str) -> int:
    if currency in LOCAL_PRICE_CENTS:
        return LOCAL_PRICE_CENTS[currency]
    # KES 15 scaled by the rough KES rate, to two significant figures.
    raw = 1_500 * countries.KES_RATE.get(currency, 1)
    if raw <= 0:
        return 0
    digits = floor(log10(raw)) - 1
    return int(round(raw / 10 ** digits) * 10 ** digits)


def usd_cents_to_local(usd_cents: int, currency: str) -> int:
    """USD to local hundredths, via the rough KES rate (KES_RATE["USD"] is USD per KES)."""
    kes_cents = usd_cents / countries.KES_RATE["USD"]
    return int(round(kes_cents * countries.KES_RATE.get(currency, 1)))


def month_bounds(period: str) -> tuple[date, date]:
    y, m = map(int, period.split("-"))
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    return start, end


def previous_period(today: date | None = None) -> str:
    first = (today or timezone.localdate()).replace(day=1)
    last = first - timedelta(days=1)
    return f"{last.year}-{last.month:02d}"


def active_members(chama: Chama, period: str) -> int:
    from apps.ledger.models import LedgerEntry

    start, end = month_bounds(period)
    return (LedgerEntry.objects.filter(chama=chama, member__isnull=False,
                                       at__date__gte=start, at__date__lte=end)
            .values("member_id").distinct().count())


def in_trial(chama: Chama, period: str) -> bool:
    if chama.activated_at is None:
        return True
    a = timezone.localtime(chama.activated_at).date()
    start, _ = month_bounds(period)
    months = (start.year - a.year) * 12 + (start.month - a.month)
    return months < FREE_MONTHS


def compute(chama: Chama, period: str) -> dict:
    """What the bill for a month is, or would be (also used for this month so far)."""
    from apps.identity.models import IdentityCheck

    start, end = month_bounds(period)
    n = active_members(chama, period)
    price = price_per_member_cents(chama.currency)
    checks = list(IdentityCheck.objects.filter(paid_by=chama, decided_at__date__gte=start,
                                               decided_at__date__lte=end))
    checks_cents = sum(usd_cents_to_local(c.price_usd_cents, chama.currency) for c in checks)
    free = "trial" if in_trial(chama, period) else "small" if n <= FREE_UP_TO_MEMBERS else ""
    members_cents = 0 if free else n * price
    return {"period": period, "currency": chama.currency, "active_members": n,
            "price_per_member_cents": price, "members_cents": members_cents,
            "id_checks": len(checks), "id_checks_cents": checks_cents,
            "total_cents": members_cents + checks_cents, "free_reason": free,
            "due_on": (end + timedelta(days=15)).isoformat()}


def issue(chama: Chama, period: str) -> Bill:
    existing = Bill.objects.filter(chama=chama, period=period).first()
    if existing:
        return existing
    c = compute(chama, period)
    try:
        with transaction.atomic():
            return Bill.objects.create(
                chama=chama, period=period, currency=c["currency"],
                active_members=c["active_members"],
                price_per_member_cents=c["price_per_member_cents"],
                members_cents=c["members_cents"], id_checks=c["id_checks"],
                id_checks_cents=c["id_checks_cents"], total_cents=c["total_cents"],
                free_reason=c["free_reason"],
                state=Bill.State.FREE if c["total_cents"] == 0 else Bill.State.OPEN,
                due_on=date.fromisoformat(c["due_on"]))
    except IntegrityError:
        return Bill.objects.get(chama=chama, period=period)


def issue_all(period: str | None = None) -> int:
    period = period or previous_period()
    n = 0
    for chama in Chama.objects.filter(state=Chama.State.ACTIVE):
        issue(chama, period)
        n += 1
    return n


def mark_paid(bill: Bill, reference: str) -> Bill:
    Bill.objects.filter(pk=bill.pk, state=Bill.State.OPEN).update(
        state=Bill.State.PAID, paid_at=timezone.now(), paid_reference=reference[:60])
    bill.refresh_from_db()
    return bill


def pay_to() -> dict:
    """Where the chama pays JiraniChama. Empty until the owner sets up a paybill."""
    return {"paybill": getattr(settings, "BILLING_PAYBILL", ""),
            "account_prefix": getattr(settings, "BILLING_ACCOUNT_PREFIX", "JC")}
