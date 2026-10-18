# Staff console

JiraniChama staff work in an internal web console at `/staff/`. Members never use it.

## Rules

- **Two-factor login from day one.** A password alone is not enough: staff must also
  enter a code from an authenticator app (TOTP). Until the code is entered, every page
  sends them back to the login page.
- **Reachable only over the staff VPN** in production; the proxy refuses other traffic.
- **Chama data is read-only.** Ledger entries, chamas and memberships can be viewed, never
  added, changed or deleted from the console. From Phase 1, viewing a chama's data also
  needs a time-limited support grant from two of its officials.
- **Every change needs a reason and a ticket reference**, typed as proper fields, and
  writes an audit entry in the same transaction. Nothing can be deleted from the console.

## Creating a staff account

```bash
python manage.py create_staff --phone 0712345678 --name "Jane Staff" --reason "New support hire" --case-ref HR-12
```

It prints a one-time password and an `otpauth://` link to add to an authenticator app.
The creation itself is written to the audit log.

## The audit log

`staff_audit_log` is chained like the ledger: each entry's hash covers the one before, so
a deleted or edited row is found by `apps.staff.audit.verify_audit_chain()`. The database
refuses UPDATE and DELETE on it.
