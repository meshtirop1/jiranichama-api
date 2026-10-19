# reconciliation

Who a bank line belongs to, and the entries it produces. See
[From a bank payment to a recorded contribution](../flows/payments.md).

- `Match`: every decision about a bank line, in order (member code, phone, treasurer,
  not me). Append-only.
- `services.find_member`, `match_credit`, `record`, `assign`, `not_me`.

Phase 2 adds debit matching, daily reconciliation against the bank balance, gaps and
unapproved-debit alerts.
