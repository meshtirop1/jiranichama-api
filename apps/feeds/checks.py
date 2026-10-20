"""Checks before a statement can be accepted (5.5 step 4).

Problems refuse the statement. Flags are allowed but shown to every member.
"""

from datetime import date, timedelta

from apps.chamas.models import BankAccount

from .models import BankTransaction, Statement
from .parsers import ParsedStatement


def _problem(code: str, message: str, **extra) -> dict:
    return {"code": code, "message": message, **extra}


# Checks that can be run on the lines we read even when some lines still need typing in.
UPLOAD_ONLY = ("account_missing", "account_mismatch", "future_dates")


def check(parsed: ParsedStatement, account: BankAccount, today: date, *,
          route: str = "") -> tuple[list[dict], list[dict]]:
    problems, flags = [], []

    if parsed.account_no is None:
        problems.append(_problem("account_missing",
                                 "The statement does not show an account number."))
    elif not account.matches(parsed.account_no):
        problems.append(_problem("account_mismatch",
                                 "This statement is for a different bank account."))

    if parsed.opening_cents + parsed.credits_cents - parsed.debits_cents != parsed.closing_cents:
        problems.append(_problem(
            "balances_do_not_add_up",
            "Opening balance plus money in minus money out does not equal the closing "
            "balance. The file may be damaged or altered: download it again from the bank."))

    running = parsed.opening_cents
    for line in parsed.lines:
        running += line.credit_cents - line.debit_cents
        if line.balance_cents is not None and line.balance_cents != running:
            problems.append(_problem("line_balance_breaks",
                                     f"Line {line.line_no}: the balance does not follow from "
                                     "the line before.", line=line.line_no))
            break

    if parsed.period_to > today:
        problems.append(_problem("future_dates", "The statement has dates in the future."))

    last = (Statement.objects.filter(bank_account=account, state=Statement.State.ACCEPTED)
            .order_by("-period_to").first())
    if last is not None:
        if parsed.period_from > last.period_to + timedelta(days=1):
            gap = _problem("gap", f"No statement covers {last.period_to + timedelta(1)} "
                                  f"to {parsed.period_from - timedelta(1)}. Upload the "
                                  "statement for those days first.")
            # An official cannot skip a period (that is where a withdrawal could hide).
            # The bank's own email is let through, flagged to every member.
            (problems if route == Statement.Route.UPLOAD else flags).append(gap)
        elif parsed.period_from == last.period_to + timedelta(days=1) and \
                parsed.opening_cents != last.closing_cents:
            problems.append(_problem(
                "opening_mismatch",
                f"The opening balance does not match the closing balance of the statement "
                f"to {last.period_to}."))
        problems += _overlap_problems(parsed, account, last)
    if route == Statement.Route.UPLOAD:
        problems += _feed_problems(parsed, account)
    return problems, flags


def _feed_problems(parsed: ParsedStatement, account: BankAccount) -> list[dict]:
    """An official's upload must agree with what the bank's own API already told us
    for the same days: same money in and out, line for line (protection plan 2026-10-20).
    A doctored PDF cannot hide a withdrawal the bank already reported."""
    from collections import Counter

    feed = BankTransaction.objects.filter(
        bank_account=account, source=BankTransaction.Source.BANK_API,
        value_date__gte=parsed.period_from, value_date__lte=parsed.period_to)
    if not feed.exists():
        return []
    covered = set(feed.values_list("value_date", flat=True))
    theirs = Counter((t.value_date, t.direction, t.amount_cents) for t in feed)
    ours = Counter(
        (line.value_date, "credit" if line.credit_cents > 0 else "debit",
         line.credit_cents or line.debit_cents)
        for line in parsed.lines if line.value_date in covered)
    if theirs != ours:
        return [_problem("differs_from_bank_feed",
                         "This statement does not match what the bank's own system "
                         "reported for the same days. Download it again from the bank.")]
    return []


def _overlap_problems(parsed: ParsedStatement, account: BankAccount, last: Statement
                      ) -> list[dict]:
    """Lines on dates an accepted statement already covers must be exactly the lines we
    accepted. Any difference may be an altered statement (5.5)."""
    start = max(parsed.period_from, Statement.objects.filter(
        bank_account=account, state=Statement.State.ACCEPTED).order_by("period_from")
        .values_list("period_from", flat=True).first())
    end = min(parsed.period_to, last.period_to)
    if start > end:
        return []
    known = dict(BankTransaction.objects.filter(
        bank_account=account, source=BankTransaction.Source.STATEMENT,
        value_date__gte=start, value_date__lte=end).values_list("external_ref", "value_date"))
    new = {line.reference: line.value_date for line in parsed.lines
           if start <= line.value_date <= end}
    missing = set(known) - set(new)
    # A statement can be cut part-way through a day, so a later one may add lines on the
    # last day the earlier one covered. Anything else that differs may be an alteration.
    extra_earlier = {ref for ref, day in new.items()
                     if ref not in known and day != last.period_to}
    if missing or extra_earlier:
        return [_problem("overlap_differs",
                         f"Between {start} and {end} this statement differs from the one "
                         "already accepted. All officials have been told.")]
    return []
