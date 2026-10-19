# Bank statements

The bank statement is the source of truth. The rule that matters most: **the person the
statement checks never holds the evidence alone** (proposal v1.1, P.2).

## Route 1: the bank emails the chama inbox (preferred)

1. The bank's e-statement email reaches the chama's inbox address, through the mail
   provider's hook (`POST /hooks/mail/{token}/inbound/`).
2. It is accepted only if SPF, DKIM and DMARC all pass, aligned to a sending domain on
   that bank's allowlist (`BANK_STATEMENT_SENDERS`).
3. The attachment is parsed and checked (below). If it passes, it is **accepted with no
   official handling the file**, and shown to members as "from the bank".
4. Otherwise it is held (`held_mail_check`) or rejected, and nothing is recorded.

**The allowlist is empty until each bank's real sending domain is confirmed**, so today
every statement email is held. Confirming Equity's, KCB's and Co-op's domains is a week-0
task in the roadmap.

## Route 2: an official uploads it

1. `POST /chamas/{id}/statements/` (multipart, with an `Idempotency-Key`). The treasurer
   cannot upload while the chama has another official.
2. The file is hashed; the same file twice is refused.
3. It is parsed and checked (below). If it passes it waits for confirmation.
4. **A different official confirms** with an SMS code that names the period and closing
   balance (`POST /statements/{id}/confirm/`, two steps).
5. On confirmation it is accepted and its lines become bank transactions.

## Checks before acceptance

| Problem (refuses) | Meaning |
| --- | --- |
| `account_missing` / `account_mismatch` | No account number, or another account's statement |
| `balances_do_not_add_up` | Opening + money in − money out ≠ closing: damaged or altered |
| `line_balance_breaks` | A line's balance does not follow from the line before |
| `future_dates` | Dates after today |
| `opening_mismatch` | Opening balance ≠ the last statement's closing balance |
| `overlap_differs` | On dates an accepted statement covers, the lines are not the same |

A **gap** between statements is allowed but flagged to every member.

## Reading the file

Phase 1 reads CSV with a parser that finds the header row by column names and reads the
account number and balances from the lines above it. **No bank format is verified yet**:
every profile is marked unverified until a real, anonymised statement from that bank is
added as a test fixture. PDFs are stored and sent to staff (`needs_parser`).

## A line we could not read (5.5)

Banks' exports sometimes have a damaged row (a date or amount the parser cannot read).
The rest of the file is still good, so the statement is not refused:

1. **Kept, not guessed.** The statement goes to `needs_typing`. The rows we could not read
   are kept exactly as the cells in the file (`unreadable_lines`). Nothing is counted yet.
   The account and dates are still checked at once; a wrong account is refused as usual.
2. **Typed in.** Any official opens the statement, looks at the bank's own copy and types
   each of those rows: date, money in or money out, description, reference and balance if
   shown (`POST /statements/{id}/typed-lines/`, all rows at once).
3. **It must add up.** The typed rows are put back in file order and the whole statement
   goes through every check again: opening plus money in minus money out must equal the
   closing balance, running balances must follow, and overlaps must agree. If anything is
   off, nothing is saved and the official sees what is wrong. A slip of one zero never
   gets through.
4. **Confirmed by someone else.** The statement then waits for confirmation by an official
   who neither uploaded it nor typed the rows. Even a statement the bank emailed needs this
   once rows were typed.
5. **Marked forever.** Typed rows are stored as `TypedLine` (append-only) and become bank
   transactions with `typed_by` set. Members see "typed in" on them, and a contribution
   from a typed row carries `typed_in: true` in its ledger entry.

If rows are unreadable **and** the file shows no opening and closing balance, we cannot
check typed rows against anything, so the file is refused as unreadable.

## Ingesting

Each line becomes a `BankTransaction`. A line already seen in an earlier statement (same
reference) is skipped, so overlapping statements never count a payment twice. Credits go
to [matching](payments.md); debits wait for Phase 2's debit matching (`debit_pending`).

## Not yet

PDF parsing, typing in a whole statement's totals when the bank's format is unknown, statement freshness alerts and
the weekly money-out rule, daily reconciliation, and bank feeds arrive in later PRs.
