# staff

The staff console and the audit log. See [Staff console](../flows/staff-console.md).

- `site.py`: the console (`/staff/`), a Django admin site that requires a verified
  authenticator code. `AuditedModelAdmin` adds required reason and ticket fields and
  writes an audit entry on save; it never allows deletes. `ReadOnlyAdmin` is used for all
  chama data.
- `audit.py`: `record(...)` writes a chained audit entry; it refuses a missing reason or
  ticket reference. `verify_audit_chain()` recomputes the chain.
- `staff_audit_log` is append-only in the database, like the ledger.
