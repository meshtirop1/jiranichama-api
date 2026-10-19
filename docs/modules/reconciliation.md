# reconciliation

Who a bank line belongs to, and the entries it produces. See
[From a bank payment to a recorded contribution](../flows/payments.md).

- `Match`: every decision about a bank line, in order (member code, phone, treasurer,
  not me). Append-only.
- `services.find_member`, `match_credit`, `record`, `assign`, `not_me`.

Phase 2 adds debit matching, daily reconciliation against the bank balance, gaps and
unapproved-debit alerts.

## Daily reconciliation (`daily.py`)

`reconcile(chama)` compares the latest accepted statement's closing balance with what the
records say (first opening balance plus every bank-moving ledger entry), writes a
`ReconciliationRun`, and opens, updates or closes the `ReconciliationGap`. A gap open more
than 7 days becomes a breach and every member gets one SMS (`alert_breach`, after commit).
`reconcile_all` runs at 06:00 (`tasks.reconcile_daily`); feeds ingest also calls
`reconcile` after every accepted statement. `summary` is what the app shows
(`GET /chamas/{id}/reconciliation/`). See [Daily reconciliation](../flows/reconciliation.md).
