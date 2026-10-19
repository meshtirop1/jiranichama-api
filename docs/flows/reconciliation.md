# Daily reconciliation (5.5)

Every morning at 06:00, and straight after any statement is accepted, each active chama's
bank is checked against its records. Every member sees the same answer on the chama's
home screen.

## The two numbers

- **The bank says**: the closing balance of the latest accepted bank statement.
- **The records say**: the first accepted statement's opening balance, plus every
  ledger entry that moved money through the bank:
  - contributions and fines paid;
  - payouts (a turn paid less arrears counts the bank's figure: the whole pot out, the
    arrears back in as contributions);
  - bank charges and unapproved debits;
  - reversals of any of those.

  These are not bank movements, so they do not count: opening balances (already in the
  first statement's opening balance), cash confirmed but not yet banked, fines raised, and
  reversals of fines raised.
- **M-Pesa payments** that no statement has shown yet are left out until one does
  ([M-Pesa paybill](paybill.md)).
- **Records-only chamas** do not record money out in the app, so statement debits waiting
  for money-out mode count as accounted for. Turning money out on brings them in.

## Match, gap, breach

- **Match**: the two are equal. Members see "Records match the bank" with the date.
- **Gap**: they differ. A `ReconciliationGap` opens with the amount and the day it
  opened, and every member sees it. The answer splits the gap into payments not yet
  matched to a member (the treasurer assigns them) and anything else (unexplained). The
  gap closes, marked explained with the day, as soon as entries make the two equal again.
- **Breach**: a gap open for **more than 7 days** becomes a breach. Every member gets an
  SMS once ("BREACH: the records and the bank of Umoja have differed by KES 4,500 since
  14 Mar…"), and it stays on everyone's home screen until it closes.

`GET /chamas/{id}/reconciliation/` returns:
- the status (`no_statement`, `match`, `open`, `breach`);
- both numbers and the gap;
- unmatched and unexplained amounts;
- when the gap opened and how many days ago;
- the last two weeks of daily gaps.

Every check is kept (`ReconciliationRun`, insert only). Gaps change only their state,
amount and dates.

## Not yet

A bank feed's live balance in place of the last statement (Phase 4 bank APIs), push
notifications alongside the SMS, and turning a breach into a dispute (5.14).

## With a bank feed (Phase 4)

When the chama's bank is read through its API, the check uses the feed's balance whenever
it is at least as recent as the last statement (`bank_source: bank_api`). See
[bank feeds](bank-feeds.md).
