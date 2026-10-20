# staff

The staff console and the audit log. See [Staff console](../flows/staff-console.md).

- `site.py`: the console (`/staff/`), a Django admin site that requires a verified
  authenticator code. `AuditedModelAdmin` adds required reason and ticket fields and
  writes an audit entry on save; it never allows deletes. `ReadOnlyAdmin` is used for all
  chama data.
- `audit.py`: `record(...)` writes a chained audit entry; it refuses a missing reason or
  ticket reference. `verify_audit_chain()` recomputes the chain.
- `staff_audit_log` is append-only in the database, like the ledger.

## Support grants (`support.py`, `support_views.py`)

`SupportGrant` (append-only except its state, confirmation, revocation and times):
`request`, `confirm` (a second official; ledger entry and SMS to every member), `revoke`,
`expire_due`, `active_grant(chama, area)`. See [support access](../flows/support-access.md).

## Sign-in form

`StaffLoginForm` hashes the phone number typed (accounts are keyed by `phone_hash`) and
picks a staff member's only authenticator automatically.

## The admin web console (`apps/console`)

Server-rendered Django views and templates at `/console/`, behind the same two-factor
sign-in. See [admin console](../flows/admin-console.md).

## Demo data (`manage.py seed_demo`)

Development only (refuses without DEBUG): a table-banking chama with a year of records for
twelve members, chaired by the demo phone (default 0700 000 001). Sign in on the app with
that number; the code appears on the dev SMS page. Used to try the app on an emulator and
to measure it with realistic data.

## ID checks and privacy (2026-10-20)

See [ID checks and privacy](../flows/identity.md).
