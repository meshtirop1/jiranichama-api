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

Meeting fines (absence 11.1, lateness 11.2) are raised when the secretary closes attendance; see [meetings](meetings.md). `Fine.meeting` links them, and they are unique per member, rule and meeting. Caps across several fine types arrive later.

### Waivers (`waivers.py`, `views.py`)

`Waiver` (append-only except its state, reason and ledger entry) and `WaiverDecision`
(append-only, one per official). `request`, `start_approve` / `approve` (SMS code naming the
member and amount), `decline`, `on_vote_closed`, `expire_due` (hourly). `deciders` never
includes the fined member. `GET /chamas/{id}/fines/` lists fines with their latest
waiver. Rules section `waivers: {officials, member_vote}`, rule 3.5. See
[fine waivers](../flows/fine-waivers.md).
