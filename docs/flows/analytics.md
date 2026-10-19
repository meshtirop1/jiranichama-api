# Numbers for running the chama (Phase 5)

`GET /chamas/{id}/analytics/?months=6` (1 to 24 months). Everything comes from the ledger
and from records every member can already see, so every member can read it, not only the
officials.

- **Month by month**: what was due (members expected that month times the contribution
  under the rules then), what was paid for it and the share paid, how much on time and
  how much late (the outcome the rules gave each payment), and fines raised, paid and
  waived.
- **Who is behind**: members with unpaid months or fines, most first (twenty at most),
  and how many in all.
- **Meetings**: attendance at each recent meeting, from its `meeting_closed` entry
  (present and late count as attended).
- **Loans** (only where loans are on): how many are running, the amount outstanding, and
  how many are behind or in default.
- **Money**: everything contributed, the bank balance from the last reconciliation, the
  investments at their latest confirmed value, and the net worth.
