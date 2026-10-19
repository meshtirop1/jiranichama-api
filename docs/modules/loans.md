# loans

Loans with guarantors (5.8), behind the `loans` flag. The flow is in
[loans](../flows/loans.md).

| File | What it does |
| --- | --- |
| `schedule.py` | Pure: loan rules with defaults (`settings_from`), `build` (flat or reducing, monthly, equal principal) |
| `models.py` | `Loan` (append-only except state, reason, proposal, disbursement date, schedule), `Guarantee` (append-only except state and answer), `LoanReminder` (one SMS per instalment and kind) |
| `services.py` | `quote`, `request`, guarantees (`start_accept`, `accept`, `decline`, `replace_guarantor`), `_to_approval` (a money-out proposal of kind `loan`), `sync`, `on_paid`, `balance`, `repayment_cents` / `write_repayment` (used by `reconciliation.apply_payment`), `settle`, `run_daily` |
| `views.py`, `tasks.py` | The loan endpoints; the daily task at 07:30 |

The chama data carries `loans_on`, so the app shows loans only where they are switched on.

Ledger types: `loan_disbursed` (negative), `loan_repayment` and `loan_interest`. Penalties
are ordinary fines under rule 12.3. `loan_disbursed` and `loan_repayment` are bank money,
so daily reconciliation counts them.

Balances come from the entries carrying `decision.loan_id`, less any reversals of them.
So "not me" on a repayment undoes it without touching the loan row.
