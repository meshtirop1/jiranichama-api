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

## Worker queues (2026-10-20)

The test-server worker must consume every queue in `CELERY_TASK_ROUTES`: `celery,feeds,rules,notify,documents,signing`. It used to consume only `celery`, so platform signatures never ran there ("207 records waiting for JiraniChama's signature"). Production workers need the same list (or one worker per queue).
