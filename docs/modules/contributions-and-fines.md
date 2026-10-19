# contributions and fines

## contributions (`projections.py`)

What a member owes and has paid, **worked out from the ledger every time**, never stored:

- `dues(member, on)`: every period from the member's first one to 12 months past `on`,
  with what is still outstanding under the rules in force at each due date.
- `position(member, on)`: contributed (including opening savings), opening savings,
  cash waiting for the bank, arrears, fines owed, credit, next due.
  `GET /memberships/{id}/position/`; every member can see every member's position.

A member's first period is the month they joined if they joined on or before the due day,
otherwise the next month.

### Opening balances and cash (`models.py`, `services.py`)

`OpeningBalance` and its lines (drafted by the treasurer, confirmed by another official),
`OpeningObjection`, and `CashReceipt` (recorded → member_confirmed → banked, or declined).
See [Opening balances and cash at meetings](../flows/opening-and-cash.md).

## fines

- `Fine`: one per member, rule and period. It is a quick view; the ledger holds the facts
  (`fine_raised`, `fine_paid`, `reversal`).
- `services.raise_late_fines(chama, today)`: raises late fines for periods whose grace
  ended. Hourly via Celery beat.

Waivers, meeting fines and caps across several fine types arrive in Phase 3.
