# Database roles

Production uses two PostgreSQL roles:

| Role | Used by | Can |
| --- | --- | --- |
| `jiranichama_owner` | Migrations only | Everything; owns the tables and triggers |
| `jiranichama_app` | Django and Celery | Read and write ordinary tables; only INSERT and SELECT on append-only tables |

After the first `migrate`, and after any migration that adds a table, run
`ops/sql/roles.sql` as the owner.

## Why two roles

The triggers already refuse UPDATE and DELETE on `ledger_entry`, `ledger_entry_signature`
and `staff_audit_log`. Removing those grants as well means that even if a trigger were
dropped, the application could still not change history. Only the owner role can alter
triggers, and it is never used by the running app.

## Tests and CI

Tests run as a single superuser so the test runner can create and reset the database
(TRUNCATE is not trapped by the triggers). The tamper tests use that superuser to bypass
the triggers on purpose and prove the chain check finds the change.
