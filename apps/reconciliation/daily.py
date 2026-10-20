"""Daily reconciliation (5.5): the bank balance against the records, every morning.

The bank says: the closing balance of the latest accepted statement.
The records say: the first accepted statement's opening balance, plus every ledger entry
that moved money through the bank (contributions, fines paid, payouts, bank charges,
unapproved debits, and reversals of those). Opening balances, cash not yet banked and
fines raised are bookkeeping, not bank movements, so they do not count.

Records-only chamas do not record money out in the app, so their statement debits that
wait for money-out mode count as accounted for.

See docs/flows/reconciliation.md.
"""

from datetime import date

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.chamas.models import Chama, Membership
from apps.core import money
from apps.feeds.models import BankTransaction, Statement
from apps.ledger.models import LedgerEntry
from apps.notify.sms import SmsFailed, send_sms

from .freshness import status as freshness_status
from .models import ReconciliationGap, ReconciliationRun

BREACH_AFTER_DAYS = 7
BANK_TYPES = ("contribution", "fine_paid", "payout", "bank_charge",
              "unapproved_debit_detected", "interest_earned", "loan_disbursed",
              "loan_repayment", "investment_income")


def _kes(cents: int, of=None) -> str:
    return money.fmt(cents, of)


def records_cents(chama: Chama, baseline: int) -> int:
    # Fines and loans settled from a leaving member's savings (exits) never touch the
    # bank: only the settlement paid out does.
    moves = (LedgerEntry.objects.filter(chama=chama, type__in=BANK_TYPES)
             .exclude(source="exit").aggregate(t=Sum("amount_cents"))["t"] or 0)
    reversed_moves = (LedgerEntry.objects.filter(chama=chama, type="reversal",
                                                 reverses__type__in=BANK_TYPES)
                      .exclude(reverses__source="exit")
                      .aggregate(t=Sum("amount_cents"))["t"] or 0)
    total = baseline + moves + reversed_moves
    # An M-Pesa payment reaches us before the statement that shows it in the bank: until
    # a statement line repeats it, it is not in the bank figure, so leave it out here too.
    covered = BankTransaction.objects.filter(duplicate_of__isnull=False) \
        .values_list("duplicate_of_id", flat=True)
    waiting_ids = BankTransaction.objects.filter(
        chama=chama, source=BankTransaction.Source.DARAJA).exclude(pk__in=covered) \
        .values_list("id", flat=True)
    ahead = (LedgerEntry.objects.filter(chama=chama, source="bank_txn",
                                        source_id__in=waiting_ids)
             .filter(type__in=BANK_TYPES).aggregate(t=Sum("amount_cents"))["t"] or 0)
    total -= ahead
    if chama.mode == Chama.Mode.RECORDS_ONLY:
        waiting = (BankTransaction.objects.filter(
            chama=chama, state=BankTransaction.State.DEBIT_PENDING)
            .aggregate(t=Sum("amount_cents"))["t"] or 0)
        total -= waiting
    return total


def reconcile(chama: Chama, today: date | None = None) -> ReconciliationRun | None:
    today = today or timezone.localdate()
    from apps.feeds.bank_feed import latest

    accepted = Statement.objects.filter(chama=chama, state=Statement.State.ACCEPTED)
    first = accepted.order_by("period_from", "created_at").first()
    last = accepted.order_by("-period_to", "-created_at").first()
    feed = latest(chama)
    if first is None and (feed is None or feed.opening_cents is None):
        return None
    baseline = first.opening_cents if first is not None else feed.opening_cents
    # The bank's balance: from its own API when that is newer than the last statement.
    use_feed = feed is not None and (
        last is None or timezone.localdate(feed.balance_at) >= last.period_to)
    bank = feed.last_balance_cents if use_feed else last.closing_cents
    with transaction.atomic():
        Chama.objects.select_for_update().get(pk=chama.pk)
        records = records_cents(chama, baseline)
        unmatched = (BankTransaction.objects.filter(
            chama=chama, state=BankTransaction.State.UNMATCHED,
            direction=BankTransaction.Direction.CREDIT)
            .exclude(source=BankTransaction.Source.DARAJA)
            .aggregate(t=Sum("amount_cents"))["t"] or 0)
        gap = bank - records
        run = ReconciliationRun.objects.create(
            chama=chama, on=today, statement=None if use_feed else last, bank_cents=bank,
            source="bank_api" if use_feed else "statement",
            records_cents=records, gap_cents=gap, unmatched_cents=unmatched)
        current = (ReconciliationGap.objects.select_for_update()
                   .filter(chama=chama, state__in=[ReconciliationGap.State.OPEN,
                                                   ReconciliationGap.State.BREACH]).first())
        breach_now = None
        from apps.payouts.models import UnapprovedDebit

        if gap == 0 and UnapprovedDebit.objects.filter(
                chama=chama, state=UnapprovedDebit.State.OPEN).exists():
            gap = None  # the figures agree, but money left without approval
        if gap is None:
            pass
        elif gap == 0:
            if current:
                current.state = ReconciliationGap.State.EXPLAINED
                current.explained_on = today
                current.save(update_fields=["state", "explained_on"])
        elif current is None:
            ReconciliationGap.objects.create(chama=chama, opened_on=today,
                                             first_gap_cents=gap, amount_cents=gap)
        else:
            current.amount_cents = gap
            fields = ["amount_cents"]
            if (current.state == ReconciliationGap.State.OPEN
                    and (today - current.opened_on).days > BREACH_AFTER_DAYS):
                current.state, current.breach_on = ReconciliationGap.State.BREACH, today
                fields += ["state", "breach_on"]
                breach_now = current.pk
            current.save(update_fields=fields)
    if breach_now:
        transaction.on_commit(lambda: alert_breach(breach_now))
    return run


def alert_breach(gap_id) -> int:
    """Every member hears when a gap has stayed open more than 7 days (5.5 step 5)."""
    gap = ReconciliationGap.objects.select_related("chama").get(pk=gap_id)
    amount, since = _kes(abs(gap.amount_cents), gap), gap.opened_on.strftime("%d %b")
    name = gap.chama.name
    sent = 0
    for m in (Membership.objects.filter(chama=gap.chama, state=Membership.State.ACTIVE)
              .select_related("user")):
        if m.user.language == "sw":
            text = (f"TAHADHARI: kumbukumbu za {name} na benki zimetofautiana kwa "
                    f"{amount} tangu {since}. Viongozi wanapaswa kueleza.")
        else:
            text = (f"BREACH: the records and the bank of {name} have differed by "
                    f"{amount} since {since}. Officials must explain it.")
        try:
            send_sms(m.user.phone, text)
            sent += 1
        except SmsFailed:
            continue
    ReconciliationGap.objects.filter(pk=gap.pk).update(alerted=sent)
    return sent


def reconcile_all(today: date | None = None) -> int:
    n = 0
    for chama in Chama.objects.filter(state=Chama.State.ACTIVE):
        if reconcile(chama, today) is not None:
            n += 1
    return n


def summary(chama: Chama) -> dict:
    """What every member's home screen shows."""
    run = (ReconciliationRun.objects.filter(chama=chama).select_related("statement")
           .order_by("-at").first())
    gap = (ReconciliationGap.objects.filter(chama=chama)
           .exclude(state=ReconciliationGap.State.EXPLAINED).first())
    from apps.payouts.models import UnapprovedDebit

    unapproved = UnapprovedDebit.objects.filter(chama=chama,
                                                state=UnapprovedDebit.State.OPEN).exists()
    if unapproved:
        status = "breach"  # money left without approval: never shown as a match
    elif run is None:
        status = "no_statement"
    elif gap is None:
        status = "match"
    else:
        status = gap.state  # open or breach
    days: dict[str, int] = {}
    for h in (ReconciliationRun.objects.filter(chama=chama).order_by("-at")
              .values("on", "gap_cents")[:60]):
        days.setdefault(h["on"].isoformat(), h["gap_cents"])  # the last check each day
    return {
        "status": status,
        "checked_on": run.on.isoformat() if run else None,
        "bank_as_of": ((run.statement.period_to if run.statement_id
                        else timezone.localdate(run.at)).isoformat() if run else None),
        "bank_source": run.source if run else None,
        "bank_cents": run.bank_cents if run else None,
        "records_cents": run.records_cents if run else None,
        "gap_cents": run.gap_cents if run else 0,
        "unmatched_cents": run.unmatched_cents if run else 0,
        "unexplained_cents": (run.gap_cents - run.unmatched_cents) if run else 0,
        "gap_opened_on": gap.opened_on.isoformat() if gap else None,
        "days_open": (timezone.localdate() - gap.opened_on).days if gap else 0,
        "breach_after_days": BREACH_AFTER_DAYS,
        "history": [{"on": k, "gap_cents": v} for k, v in list(days.items())[:14]],
        "statement": freshness_status(chama),
    }
