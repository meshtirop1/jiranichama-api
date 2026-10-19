"""Statement freshness (5.5): is the chama's bank statement overdue?

The rules say how often a statement must be accepted (``statements.every_days``, rule
5.1). Counting from the end of the last accepted statement (or from when the chama
became active, before any):

- level 1, overdue: the treasurer is reminded;
- level 2, 7 more days: every official is reminded;
- level 3, 14 more days: every member sees "Records not checked against the bank
  since {date}".

The app shows each level to the people it is for. Until push notifications exist, the
treasurer (level 1) and the officials (level 2) also get one SMS each per overdue
spell; members are not texted. See docs/flows/statement-reminders.md.
"""

from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.chamas.models import OFFICIAL_ROLES, Chama, Membership
from apps.feeds.models import Statement
from apps.notify.sms import SmsFailed, send_sms
from apps.rules.models import version_at

from .models import StatementReminder

OFFICIALS_AFTER = 7
EVERYONE_AFTER = 14


def status(chama: Chama, today: date | None = None) -> dict:
    today = today or timezone.localdate()
    version = version_at(chama, today)
    every = version.rules["statements"]["every_days"] if version else 14
    last = (Statement.objects.filter(chama=chama, state=Statement.State.ACCEPTED)
            .order_by("-period_to").first())
    if last is not None:
        since = last.period_to
    elif chama.activated_at:
        since = timezone.localdate(chama.activated_at)
    else:
        since = today
    due_on = since + timedelta(days=every)
    days_overdue = max((today - due_on).days, 0)
    if days_overdue == 0:
        level = 0
    elif days_overdue >= EVERYONE_AFTER:
        level = 3
    elif days_overdue >= OFFICIALS_AFTER:
        level = 2
    else:
        level = 1
    return {"checked_to": since.isoformat() if last else None, "every_days": every,
            "due_on": due_on.isoformat(), "days_overdue": days_overdue, "level": level}


def _text(m: Membership, chama: Chama, days: int, level: int) -> str:
    if m.user.language == "sw":
        return (f"JiraniChama: taarifa ya benki ya {chama.name} imechelewa siku {days}. "
                "Ipakie au iombe benki leo.")
    who = "Please upload it" if level == 1 else "An official must upload it"
    return (f"JiraniChama: the bank statement for {chama.name} is {days} day(s) overdue. "
            f"{who} today.")


def remind(chama: Chama, today: date | None = None) -> int:
    """Send this overdue spell's reminder for the current level, once. Returns how many
    were texted."""
    s = status(chama, today)
    if s["level"] not in (1, 2):
        return 0
    roles = ("treasurer",) if s["level"] == 1 else OFFICIAL_ROLES
    try:
        with transaction.atomic():
            reminder = StatementReminder.objects.create(
                chama=chama, due_on=date.fromisoformat(s["due_on"]), level=s["level"])
    except IntegrityError:
        return 0  # already reminded at this level for this spell
    sent = 0
    for m in (Membership.objects.filter(chama=chama, role__in=roles,
                                        state=Membership.State.ACTIVE).select_related("user")):
        try:
            send_sms(m.user.phone, _text(m, chama, s["days_overdue"], s["level"]))
            sent += 1
        except SmsFailed:
            continue
    StatementReminder.objects.filter(pk=reminder.pk).update(sent=sent)
    return sent


def remind_all(today: date | None = None) -> int:
    return sum(remind(c, today) for c in Chama.objects.filter(state=Chama.State.ACTIVE))
