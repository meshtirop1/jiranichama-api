"""A header-detecting CSV statement parser.

It finds the header row by column names, reads the preamble above it for the account
number and balances, and turns amounts into integer cents with Decimal (never float).
Statements listed newest first are put in date order.
"""

import csv
import hashlib
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .base import ParsedLine, ParsedStatement, StatementUnreadable
from .profiles import Profile

ALIASES: dict[str, tuple[str, ...]] = {
    "value_date": ("value date", "val date"),
    "date": ("transaction date", "trans date", "txn date", "posting date", "date",
             "tran date", "book date"),
    "narration": ("narration", "description", "details", "particulars",
                  "transaction details", "narrative", "transaction description"),
    "reference": ("reference", "ref", "ref no", "reference no", "transaction ref",
                  "transaction id", "cheque no", "customer reference"),
    "debit": ("debit", "debits", "withdrawal", "withdrawals", "money out", "paid out", "dr",
              "debit amount"),
    "credit": ("credit", "credits", "deposit", "deposits", "money in", "paid in", "cr",
               "credit amount"),
    "balance": ("balance", "running balance", "book balance", "available balance",
                "ledger balance"),
}

_DATE_FORMATS_DAY_FIRST = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d %b %Y",
                           "%d-%b-%Y", "%d %B %Y", "%d/%m/%y", "%d-%b-%y", "%d %b %y")
# Labels and values may be separated by ":", "-" or a CSV comma ("Account Number:,0123…").
_SEP = r"\s*[:,\-]*\s*"
_AMOUNT = r"(?:kes|ksh|kshs)?\.?\s*([\-(]?[\d,]+(?:\.\d{1,2})?\)?)"
_ACCOUNT = re.compile(r"account\s*(?:no|number|num|#)?\.?" + _SEP + r"([0-9][0-9 \-]{5,24})",
                      re.I)
_OPENING = re.compile(r"opening\s*balance" + _SEP + _AMOUNT, re.I)
_CLOSING = re.compile(r"closing\s*balance" + _SEP + _AMOUNT, re.I)


def _norm(cell: str) -> str:
    return re.sub(r"[^a-z ]", "", (cell or "").strip().lower()).strip()


def to_cents(raw: str) -> int:
    s = (raw or "").strip().replace(",", "").replace(" ", "")
    s = re.sub(r"^(kes|ksh|kshs)\.?", "", s, flags=re.I)
    if s in ("", "-", "--"):
        return 0
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if s.endswith("DR") or s.endswith("Dr"):
        negative, s = True, s[:-2]
    elif s.endswith(("CR", "Cr")):
        s = s[:-2]
    try:
        value = Decimal(s)
    except InvalidOperation as exc:
        raise ValueError(f"not an amount: {raw!r}") from exc
    cents = value * 100
    if cents != cents.to_integral_value():
        raise ValueError(f"more than two decimal places: {raw!r}")
    cents = int(cents)
    return -cents if negative else cents


def to_date(raw: str, profile: Profile) -> date:
    s = (raw or "").strip()
    s = s.split(" ")[0] if re.match(r"^\d{1,4}[/\-.]\d{1,2}[/\-.]\d{2,4} \d", s) else s
    formats = _DATE_FORMATS_DAY_FIRST if profile.day_first else (
        "%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d")
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"not a date: {raw!r}")


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise StatementUnreadable("The file is not text.")


def _find_columns(row: list[str], aliases: dict[str, tuple[str, ...]]) -> dict[str, int] | None:
    cells = [_norm(c) for c in row]
    found: dict[str, int] = {}
    for key, names in aliases.items():
        for i, cell in enumerate(cells):
            if cell in names and i not in found.values():
                found[key] = i
                break
    has_date = "value_date" in found or "date" in found
    has_amounts = "debit" in found and "credit" in found
    return found if has_date and has_amounts else None


def _line_ref(value_date: date, narration: str, credit: int, debit: int, balance,
              seen: dict) -> str:
    """A stable reference for banks that give none, so overlapping uploads of the same
    line are recognised. Identical lines on the same day are numbered in file order."""
    base = f"{value_date.isoformat()}|{narration}|{credit}|{debit}|{balance}"
    n = seen.get(base, 0)
    seen[base] = n + 1
    return "jc:" + hashlib.sha256(f"{base}|{n}".encode()).hexdigest()[:32]


def parse_csv(data: bytes, profile: Profile) -> ParsedStatement:
    text = _decode(data)
    rows = list(csv.reader(io.StringIO(text)))
    aliases = {k: v + profile.extra_aliases.get(k, ()) for k, v in ALIASES.items()}

    header_at, cols = None, None
    for i, row in enumerate(rows[:60]):
        cols = _find_columns(row, aliases)
        if cols:
            header_at = i
            break
    if header_at is None:
        raise StatementUnreadable("We could not find the column headings (date, debit, "
                                  "credit) in this file.")

    preamble = "\n".join(",".join(r) for r in rows[:header_at])
    account = _ACCOUNT.search(preamble)
    opening = _OPENING.search(preamble)
    closing = _CLOSING.search(preamble)
    # Some banks put the balances below the table instead.
    tail = "\n".join(",".join(r) for r in rows[-10:])
    opening = opening or _OPENING.search(tail)
    closing = closing or _CLOSING.search(tail)

    date_col = cols.get("value_date", cols.get("date"))
    lines, bad = [], []
    for i, row in enumerate(rows[header_at + 1:], start=header_at + 2):
        if not any(c.strip() for c in row):
            continue
        cell = row[date_col] if date_col < len(row) else ""
        if re.search(r"opening|closing|total|balance b/?f|balance c/?f", " ".join(row), re.I) \
                and not re.match(r"^\s*\d", cell):
            continue
        try:
            value_date = to_date(cell, profile)
            credit = to_cents(row[cols["credit"]]) if cols["credit"] < len(row) else 0
            debit = to_cents(row[cols["debit"]]) if cols["debit"] < len(row) else 0
            balance = (to_cents(row[cols["balance"]])
                       if "balance" in cols and cols["balance"] < len(row)
                       and row[cols["balance"]].strip() else None)
        except ValueError:
            bad.append(i)
            continue
        if credit < 0 or debit < 0:
            # A negative in a credit column is a debit, and the reverse.
            credit, debit = (max(credit, 0) + max(-debit, 0), max(debit, 0) + max(-credit, 0))
        if credit == 0 and debit == 0:
            continue
        narration = row[cols["narration"]].strip() if "narration" in cols and \
            cols["narration"] < len(row) else ""
        reference = row[cols["reference"]].strip() if "reference" in cols and \
            cols["reference"] < len(row) else ""
        lines.append(ParsedLine(i, value_date, narration, reference, credit, debit, balance))

    if bad:
        raise StatementUnreadable("Some lines could not be read.", lines=bad)
    if not lines:
        raise StatementUnreadable("The statement has no transactions.")

    if lines[0].value_date > lines[-1].value_date:
        lines.reverse()  # newest first in the file

    seen: dict = {}
    for line in lines:
        if not line.reference:
            line.reference = _line_ref(line.value_date, line.narration, line.credit_cents,
                                       line.debit_cents, line.balance_cents, seen)

    if opening and closing:
        opening_cents, closing_cents = to_cents(opening.group(1)), to_cents(closing.group(1))
    elif lines[0].balance_cents is not None and lines[-1].balance_cents is not None:
        first = lines[0]
        opening_cents = first.balance_cents - first.credit_cents + first.debit_cents
        closing_cents = lines[-1].balance_cents
    else:
        raise StatementUnreadable("The statement must show the opening and closing balance "
                                  "or a balance on every line.")

    return ParsedStatement(
        parser=f"csv:{profile.bank}{'' if profile.verified else ':unverified'}",
        account_no="".join(ch for ch in account.group(1) if ch.isdigit()) if account else None,
        period_from=lines[0].value_date,
        period_to=lines[-1].value_date,
        opening_cents=opening_cents,
        closing_cents=closing_cents,
        lines=lines,
    )
