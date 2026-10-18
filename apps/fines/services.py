"""Raising late-contribution fines on schedule (5.9)."""

from datetime import date

from django.db import IntegrityError, transaction

from apps.chamas.models import Chama, Membership
from apps.contributions.projections import dues
from apps.ledger import services as ledger
from apps.rules import calendar, engine
from apps.rules.models import version_at

from .models import Fine


def raise_late_fines(chama: Chama, today: date) -> int:
    """For every period whose grace ended before ``today`` and is still unpaid, raise the
    late fine once. Judged under the rules in force on that period's due date."""
    if chama.state != Chama.State.ACTIVE:
        return 0
    current = version_at(chama, today)
    if current is None:
        return 0
    raised = 0
    members = Membership.objects.filter(chama=chama, state__in=[
        Membership.State.ACTIVE, Membership.State.PROBATION])
    for m in members:
        for due in dues(m, today):
            if due.outstanding_cents <= 0 or due.period > calendar.period_of(today):
                continue
            version = version_at(chama, calendar.due_date(due.period, current.rules)) or current
            rules = version.rules
            if calendar.grace_end(due.period, rules) >= today:
                continue
            amount = engine.late_fine_cents(rules)
            if amount <= 0:
                continue
            if Fine.objects.filter(membership=m, rule_ref=engine.REF_LATE_FINE,
                                   period=due.period).exists():
                continue
            try:
                with transaction.atomic():
                    entry = ledger.append(
                        chama=chama, type="fine_raised", source="scheduler", member=m,
                        amount_cents=amount, period=due.period, rule_version_id=version.id,
                        rule_refs=[engine.REF_LATE_FINE],
                        decision={"cause": "late_contribution",
                                  "due": calendar.due_date(due.period, rules).isoformat(),
                                  "grace_end": calendar.grace_end(due.period, rules).isoformat(),
                                  "outstanding_cents": due.outstanding_cents},
                    )
                    Fine.objects.create(chama=chama, membership=m,
                                        rule_ref=engine.REF_LATE_FINE, period=due.period,
                                        amount_cents=amount, raised_entry=entry)
            except IntegrityError:
                continue  # raised by a parallel run
            raised += 1
    return raised
