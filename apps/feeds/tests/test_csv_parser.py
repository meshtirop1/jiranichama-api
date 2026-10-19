"""The CSV parser on synthetic statements. These are NOT real bank formats; real
anonymised exports from each pilot bank go under tests/fixtures/statements/ before a
profile is marked verified."""

from datetime import date

import pytest

from apps.feeds.parsers import NeedsParser, StatementUnreadable, parse
from apps.feeds.parsers.csv_parser import to_cents

PREAMBLE_STYLE = b"""Bank statement
Account Name:,UMOJA WOMEN GROUP
Account Number:,0123 4567 89012
Opening Balance:,"1,000,000.00"
Closing Balance:,"1,149,500.00"

Value Date,Narration,Reference,Debit,Credit,Balance
04/03/2026,MPESA C2B JC-4821-002 WANJIRU,QX12AB,,"1,000.00","1,001,000.00"
05/03/2026,CASH DEPOSIT,,,"150,000.00","1,151,000.00"
06/03/2026,LEDGER FEE,,"1,500.00",,"1,149,500.00"
"""

NEWEST_FIRST_NO_PREAMBLE = b"""Transaction Date,Description,Money Out,Money In,Running Balance
06-Mar-2026,Charges,15.00,,1985.00
05-Mar-2026,Deposit 0712345678,,1000.00,2000.00
"""


def test_reads_preamble_columns_and_amounts_in_cents():
    s = parse("equity", "march.csv", PREAMBLE_STYLE)
    assert s.account_no == "0123456789012"
    assert (s.opening_cents, s.closing_cents) == (100_000_000, 114_950_000)
    assert (s.period_from, s.period_to) == (date(2026, 3, 4), date(2026, 3, 6))
    assert [(line.credit_cents, line.debit_cents) for line in s.lines] == [
        (100_000, 0), (15_000_000, 0), (0, 150_000)]
    assert s.lines[0].reference == "QX12AB"
    assert s.lines[1].reference.startswith("jc:")  # no bank reference: a stable one is made
    assert s.parser == "csv:equity:unverified"


def test_newest_first_is_put_in_order_and_balances_are_derived():
    s = parse("kcb", "s.csv", NEWEST_FIRST_NO_PREAMBLE)
    assert [line.value_date for line in s.lines] == [date(2026, 3, 5), date(2026, 3, 6)]
    assert (s.opening_cents, s.closing_cents) == (100_000, 198_500)
    assert s.account_no is None


def test_made_up_references_are_stable_across_uploads():
    a = parse("kcb", "a.csv", NEWEST_FIRST_NO_PREAMBLE)
    b = parse("kcb", "b.csv", NEWEST_FIRST_NO_PREAMBLE)
    assert [x.reference for x in a.lines] == [y.reference for y in b.lines]


def test_unreadable_lines_are_reported_by_number():
    bad = PREAMBLE_STYLE.replace(b'05/03/2026,CASH', b'yesterday,CASH')
    with pytest.raises(StatementUnreadable) as exc:
        parse("equity", "x.csv", bad)
    assert exc.value.lines == [9]


def test_pdf_goes_to_staff():
    with pytest.raises(NeedsParser):
        parse("equity", "march.pdf", b"%PDF-1.7 ...")


def test_no_headers_is_unreadable():
    with pytest.raises(StatementUnreadable):
        parse("equity", "x.csv", b"just,some,text\n1,2,3\n")


@pytest.mark.parametrize("raw,cents", [("1,234.50", 123_450), ("(200.00)", -20_000),
                                       ("", 0), ("-", 0), ("KES 1,000", 100_000),
                                       ("500.00 DR", -50_000), ("7", 700)])
def test_to_cents(raw, cents):
    assert to_cents(raw) == cents


def test_to_cents_refuses_fractions_of_a_cent():
    with pytest.raises(ValueError):
        to_cents("1.005")
