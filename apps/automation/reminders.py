"""Payment reminders and "what do I owe, how do I pay" (owner request 2026-10-21).

Every morning at 08:00, for each active chama, every member who still owes something is
told, once per month and stage, with the exact amount and their member code:
- ``soon``: ``days_before`` days before the due date (default 3);
- ``today``: on the due date;
- ``late``: the day after the grace period ends, while it is still unpaid.

The rules' optional ``reminders`` section sets this (rule 3.7); a chama can turn any stage
off. App users get the notice in the app; members who do not use the app get an SMS.
"""

from datetime import date, timedelta

from django.utils import timezone

from apps.chamas.models import Chama, Membership
from apps.chamas.services import active_bank_account
from apps.contributions.projections import (
    _version_on,
    dues,
    open_fines,
    paid_by_period,
    rule_timeline,
)
from apps.core import money
from apps.rules import calendar

from .notices import notify

DEFAULTS = {"days_before": 3, "on_due_day": True, "after_grace": True}


def settings_from(rules: dict) -> dict:
    return {**DEFAULTS, **(rules.get("reminders") or {})}


def owed(member: Membership, on, *, timeline=None) -> dict:
    """What the member should pay now: months behind, fines, and the month falling due
    (the next one whose due date is within a month). Whole cents."""
    timeline = timeline if timeline is not None else rule_timeline(member.chama)
    version = _version_on(timeline, on)
    if version is None:
        return {"total_cents": 0, "arrears_cents": 0, "fines_cents": 0, "current_cents": 0,
                "current_period": None, "due_on": None}
    rules = version.rules
    paid = paid_by_period(member)
    all_dues = dues(member, on, timeline=timeline, paid=paid)
    current = calendar.period_of(on)
    if calendar.grace_end(current, rules) < on:
        current = calendar.next_period(current)  # this month is past: the next one is due
    arrears = sum(d.outstanding_cents for d in all_dues if d.period < current)
    now = next((d for d in all_dues if d.period == current), None)
    fines = sum(f.outstanding_cents for f in open_fines(member))
    current_cents = now.outstanding_cents if now else 0
    return {"total_cents": arrears + fines + current_cents, "arrears_cents": arrears,
            "fines_cents": fines, "current_cents": current_cents, "current_period": current,
            "due_on": calendar.due_date(current, rules).isoformat()}


def how_to_pay(chama: Chama, member: Membership, lang: str = "en") -> dict:
    """Where the money goes, in words, with the member code as the reference."""
    from apps.feeds import stk

    account = active_bank_account(chama)
    code = member.member_code
    out = {"member_code": code, "paybill": "", "till": "", "bank": "", "account_last4": "",
           "pay_now": stk.channel_for_chama(chama) is not None}
    if account is None:
        out["text"] = ""
        return out
    out.update(paybill=account.paybill, till=account.till, bank=account.get_bank_display(),
               account_last4=account.account_no_last4)
    sw = lang == "sw"
    if account.paybill:
        out["text"] = (f"Lipa kwa Paybill {account.paybill}, akaunti {code}." if sw else
                       f"Pay to Paybill {account.paybill}, account {code}.")
    elif account.till:
        out["text"] = (f"Lipa kwa Till {account.till} na utume {code} kwa mweka hazina." if sw
                       else f"Pay to Till {account.till} and send {code} to the treasurer.")
    else:
        out["text"] = (f"Weka benki {account.get_bank_display()}, akaunti inayoishia "
                       f"{account.account_no_last4}, kumbukumbu {code}." if sw else
                       f"Deposit at {account.get_bank_display()}, account ending "
                       f"{account.account_no_last4}, reference {code}.")
    return out


def _previous(period: str) -> str:
    year, month = calendar.parse_period(period)
    return f"{year - (month == 1):04d}-{(month - 2) % 12 + 1:02d}"


def _stage(today, rules) -> tuple[str, str] | None:
    """Which reminder is due today, and for which month."""
    s = settings_from(rules)
    p = calendar.period_of(today)
    for period in (p, calendar.next_period(p)):
        due = calendar.due_date(period, rules)
        if s["days_before"] and today == due - timedelta(days=s["days_before"]):
            return "soon", period
        if s["on_due_day"] and today == due:
            return "today", period
    if s["after_grace"]:
        # The grace period can run into the next month (due on the 28th, 14 days' grace).
        for period in (_previous(p), p):
            if today == calendar.grace_end(period, rules) + timedelta(days=1):
                return "late", period
    return None


def _month(period: str | None) -> str:
    if not period:
        return ""
    year, month = calendar.parse_period(period)
    return date(year, month, 1).strftime("%b")


def _text(stage: str, chama: Chama, member: Membership, o: dict, period: str) -> dict:
    total = money.fmt(o["total_cents"], chama)
    when = _month(period)
    en_pay = how_to_pay(chama, member, "en")["text"]
    sw_pay = how_to_pay(chama, member, "sw")["text"]
    if stage == "soon":
        return {"en": f"Reminder: {total} is due on {o['due_on']} ({when}). {en_pay}",
                "sw": f"Kumbusho: {total} zinadaiwa tarehe {o['due_on']} ({when}). {sw_pay}"}
    if stage == "today":
        return {"en": f"Today is the day: {total} is due. {en_pay}",
                "sw": f"Leo ndiyo siku: {total} zinadaiwa. {sw_pay}"}
    return {"en": f"Your contribution for {when} is late. You owe {total} in all. {en_pay}",
            "sw": f"Mchango wako wa {when} umechelewa. Unadaiwa {total} kwa jumla. {sw_pay}"}


def payment_reminders(today=None) -> int:
    today = today or timezone.localdate()
    sent = 0
    for chama in Chama.objects.filter(state=Chama.State.ACTIVE):
        timeline = rule_timeline(chama)
        version = _version_on(timeline, today)
        if version is None:
            continue
        found = _stage(today, version.rules)
        if found is None:
            continue
        stage, period = found
        for m in (Membership.objects.filter(chama=chama, state__in=[
                Membership.State.ACTIVE, Membership.State.PROBATION]).select_related("user")):
            o = owed(m, today, timeline=timeline)
            if o["total_cents"] <= 0 or (stage == "late" and o["arrears_cents"] <= 0):
                continue
            if notify(m.user, chama=chama, kind="due", key=f"due-{stage}:{period}:{m.id}",
                      text=_text(stage, chama, m, o, period), sms="away", urgent=stage == "late",
                      data={"screen": "pay", "chama_id": str(chama.id)}):
                sent += 1
    return sent
