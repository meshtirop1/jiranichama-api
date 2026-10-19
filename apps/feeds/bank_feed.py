"""Reading a chama's bank account through the bank's API (Phase 4). See
docs/flows/bank-feeds.md.

Every pull reads the lines since a few days before the last pull (banks post late), and the
balance. A line becomes a bank transaction with source "bank_api" and goes through the
same matching as a statement line: credits by member code or phone, debits by money out.
The same line may arrive twice, from the feed and later from a statement (or an M-Pesa
notification first): whichever arrives second is stored as a duplicate of the first, never
counted again. The balance goes to the daily reconciliation.
"""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .jenga import Client, JengaError
from .models import BankFeed, BankFeedPull, BankTransaction

OVERLAP_DAYS = 3


def twin(*, bank_account, source: str, direction: str, amount_cents: int, value_date,
         reference: str) -> BankTransaction | None:
    """The same bank line already recorded from the other place (feed or statement): by
    the bank's reference, otherwise by amount, direction and day when only one fits."""
    other = (BankTransaction.Source.STATEMENT if source == BankTransaction.Source.BANK_API
             else BankTransaction.Source.BANK_API)
    qs = BankTransaction.objects.filter(bank_account=bank_account, source=other,
                                        direction=direction, amount_cents=amount_cents,
                                        duplicate_of__isnull=True)
    covered = BankTransaction.objects.filter(duplicate_of__isnull=False) \
        .values_list("duplicate_of_id", flat=True)
    qs = qs.exclude(pk__in=covered)
    if reference:
        found = qs.filter(external_ref=reference).first()
        if found:
            return found
    same_day = list(qs.filter(value_date=value_date))
    return same_day[0] if len(same_day) == 1 else None


def client_for(feed: BankFeed) -> Client:
    return Client(feed.credentials())


def pull(feed: BankFeed, *, client: Client | None = None, today=None) -> BankFeedPull:
    from apps.payouts.services import process_debit
    from apps.reconciliation.daily import reconcile
    from apps.reconciliation.services import match_credit

    from .daraja import daraja_twin

    today = today or timezone.localdate()
    start = feed.start_date
    if feed.last_pulled_at:
        start = max(start, timezone.localdate(feed.last_pulled_at) - timedelta(days=OVERLAP_DAYS))
    try:
        client = client or client_for(feed)
        lines = client.statement(feed.country_code, feed.account_number, start, today)
        balance = client.balance_cents(feed.country_code, feed.account_number)
    except (JengaError, OSError, ValueError) as exc:
        BankFeed.objects.filter(pk=feed.pk).update(last_error=str(exc)[:300])
        return BankFeedPull.objects.create(feed=feed, error=str(exc)[:300])

    created = []
    for line in lines:
        credit = line["direction"] == "credit"
        txn = BankTransaction(
            chama=feed.chama, bank_account=feed.bank_account,
            source=BankTransaction.Source.BANK_API, external_ref=line["reference"][:100],
            direction=(BankTransaction.Direction.CREDIT if credit
                       else BankTransaction.Direction.DEBIT),
            amount_cents=line["amount_cents"], value_date=line["value_date"],
            balance_cents=line["balance_cents"],
            state=(BankTransaction.State.UNMATCHED if credit
                   else BankTransaction.State.DEBIT_PENDING))
        txn.set_narration(line["narration"])
        first = twin(bank_account=feed.bank_account, source=txn.source,
                     direction=txn.direction, amount_cents=txn.amount_cents,
                     value_date=txn.value_date, reference=txn.external_ref)
        if first is None and credit:
            first = daraja_twin(bank_account=feed.bank_account, amount_cents=txn.amount_cents,
                                value_date=txn.value_date, narration=line["narration"],
                                reference=line["reference"])
        if first is not None:
            txn.state, txn.duplicate_of = BankTransaction.State.DUPLICATE, first
        try:
            with transaction.atomic():
                txn.save()
        except IntegrityError:
            continue  # this line came in an earlier pull
        created.append(txn)
    for txn in created:
        if txn.state == BankTransaction.State.DUPLICATE:
            continue
        if txn.direction == BankTransaction.Direction.CREDIT:
            match_credit(txn)
        else:
            process_debit(txn)

    now = timezone.now()
    fields = {"last_balance_cents": balance, "balance_at": now, "last_pulled_at": now,
              "last_error": ""}
    if feed.opening_cents is None:
        # The balance before the first line we read: where the records start from when
        # the chama has no accepted statement.
        net = sum(t["amount_cents"] if t["direction"] == "credit" else -t["amount_cents"]
                  for t in lines)
        fields["opening_cents"] = balance - net
    BankFeed.objects.filter(pk=feed.pk).update(**fields)
    p = BankFeedPull.objects.create(feed=feed, balance_cents=balance, lines=len(lines),
                                    new_lines=len(created))
    reconcile(feed.chama)
    return p


def pull_all() -> int:
    n = 0
    for feed in BankFeed.objects.filter(active=True).select_related("chama", "bank_account"):
        if not pull(feed).error:
            n += 1
    return n


def latest(chama) -> BankFeed | None:
    return (BankFeed.objects.filter(chama=chama, active=True)
            .filter(~Q(last_balance_cents=None)).order_by("-balance_at").first())
