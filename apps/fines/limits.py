"""Where fining stops (owner decision 2026-10-20).

A member who falls behind is fined for at most a set number of missed months (default 3,
rule ``fines.stop_after_missed_months``). After that no more fines of any kind are raised
for them: the late-contribution fine, meeting fines and loan penalties all stop, and the
member and the officials are told, so the group settles it by talking (a payment plan, a
waiver vote, or the exit process) instead of by fines that only grow.

Separately, the rule book's monthly cap (``fines.late_contribution.monthly_cap_cents``)
limits the total of all fines raised for one member in one month.
"""

from datetime import date

from django.db.models import Q, Sum

from apps.chamas.models import OFFICIAL_ROLES, Membership
from apps.contributions.projections import dues
from apps.notify.sms import SmsFailed, send_sms
from apps.rules import calendar
from apps.rules.models import version_at

from .models import Fine, FineStop

DEFAULT_STOP_AFTER = 3


def stop_after(rules: dict) -> int:
    return int(rules["fines"].get("stop_after_missed_months", DEFAULT_STOP_AFTER))


def missed_periods(membership: Membership, today: date) -> list[str]:
    """Months whose grace has ended and that are still not paid in full, oldest first."""
    chama = membership.chama
    current = version_at(chama, today)
    if current is None:
        return []
    out = []
    for d in dues(membership, today):
        if d.outstanding_cents <= 0 or d.period > calendar.period_of(today):
            continue
        rules = (version_at(chama, calendar.due_date(d.period, current.rules)) or current).rules
        if calendar.grace_end(d.period, rules) < today:
            out.append(d.period)
    return out


def stopped(membership: Membership, today: date) -> bool:
    """True once the member has missed as many months as the rules allow fines for."""
    current = version_at(membership.chama, today)
    if current is None:
        return False
    return len(missed_periods(membership, today)) >= stop_after(current.rules)


def room(membership: Membership, rules: dict, period: str) -> int | None:
    """How much more may be fined to this member for ``period`` under the monthly cap,
    counting every kind of fine. None when the rules set no cap."""
    cap = rules["fines"]["late_contribution"]["monthly_cap_cents"]
    if cap is None:
        return None
    raised = (Fine.objects.filter(membership=membership, period=period)
              .filter(~Q(state=Fine.State.REVERSED))
              .aggregate(t=Sum("amount_cents"))["t"] or 0)
    return max(cap - raised, 0)


def capped(membership: Membership, rules: dict, period: str, amount: int) -> int:
    left = room(membership, rules, period)
    return amount if left is None else min(amount, left)


def note_stop(membership: Membership, period: str, rules: dict) -> bool:
    """Records that ``period`` was not fined because fining has stopped, so it is never
    fined later either. Tells the member and the officials the first time. Returns True
    when this is a new stop."""
    _, created = FineStop.objects.get_or_create(
        chama=membership.chama, membership=membership, period=period)
    if created and FineStop.objects.filter(membership=membership).count() == 1:
        _tell(membership, stop_after(rules))
    return created


def _tell(m: Membership, months: int) -> None:
    name = m.user.display_name or m.member_code
    to_member = {
        "en": f"JiraniChama: {m.chama.name}. You have missed {months} monthly contributions, "
              "so no more fines will be added. Please talk to your officials about a "
              "payment plan.",
        "sw": f"JiraniChama: {m.chama.name}. Umekosa michango {months} ya kila mwezi, kwa "
              "hiyo hakuna faini zaidi zitakazoongezwa. Tafadhali ongea na viongozi wako "
              "kuhusu mpango wa kulipa.",
    }
    to_officials = {
        "en": f"JiraniChama: {m.chama.name}. {name} ({m.member_code}) has missed {months} "
              "monthly contributions. Fines have stopped. Agree a payment plan, a waiver or "
              "the exit process with them.",
        "sw": f"JiraniChama: {m.chama.name}. {name} ({m.member_code}) amekosa michango "
              f"{months} ya kila mwezi. Faini zimesimamishwa. Kubalianeni naye mpango wa "
              "kulipa, msamaha au utaratibu wa kuondoka.",
    }
    officials = Membership.objects.filter(chama=m.chama, role__in=OFFICIAL_ROLES,
                                          state=Membership.State.ACTIVE).exclude(pk=m.pk)
    for who, text in [(m, to_member), *[(o, to_officials) for o in officials]]:
        try:
            send_sms(who.user.phone, text.get(who.user.language, text["en"]))
        except SmsFailed:
            pass
