# Support access (5.16)

By default JiraniChama staff cannot see any chama's records.

1. An official taps **Get help** in the app and chooses what support may read (bank
   statements, rules, records, members, disputes), for how long (default 72 hours, at most
   14 days), and why (`POST /chamas/{id}/support/`). Only one request can be open at a
   time.
2. **A second official confirms** (`POST /support-grants/{id}/confirm/`). The one who
   asked cannot. The grant becomes active, a `support_access` entry goes on the ledger
   (areas, until when, why, who asked and confirmed), and **every member gets an SMS**. The
   app shows every member the notice while it lasts.
3. Staff open the chama's records in the [admin console](admin-console.md), read-only and
   only the areas granted, each opening logged with a reason and ticket number.
4. Access **ends by itself** at the end time, or earlier when **any official** revokes it
   (`POST /support-grants/{id}/revoke/`, also on the ledger).

`GET /chamas/{id}/support/` lists the chama's recent grants for every member.

States: `requested` → `active` → `expired` or `revoked`.

## ID checks and privacy (2026-10-20)

See [ID checks and privacy](identity.md).
