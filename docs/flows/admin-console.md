# The admin web console (6.11)

The only web part of JiraniChama besides the public statement check. It is for
JiraniChama staff, at `/console/`. Members and officials use the phone app.

## Signing in

Staff sign in at `/staff/login/` with their **phone number**, password and the code from
their authenticator app (two-factor, TOTP). With a single authenticator it is chosen
automatically. Anyone not signed in with a verified code is sent to the sign-in page.
Staff accounts are made with `manage.py create_staff` (which prints the one-time password
and the authenticator link). In production the console is reachable only over the staff
VPN, enforced at the proxy.

## What staff see

- **Dashboard**: platform totals only: chamas and their states, active members, people who
  used the app this week, records written today, SMS and USSD use, the work queues with
  their counts, and a warning if the audit log's chain is broken.
- **Queues**
  - *Statements*: bank emails that failed the sender checks and files no parser reads yet.
  - *Integrity*: reconciliation breaches (more than 7 days), failing bank feeds, the last
    public anchor (OpenTimestamps state, emailed or not), the audit log chain.
  - *Support access*: chamas that asked for help, what for, until when.
  - *Referrals*: introductions the members voted for, the exact hand-over, and "Mark
    sent".
- **Chamas**: name, prefix, type, state and size. A chama's page shows its flags and
  feeds. **Its records open only inside an active support grant** from that chama
  ([support access](support-access.md)), and only after the staff member enters a reason
  and a ticket number, which go to the audit log (`view:chama`). The opening lasts an hour
  in that session and shows only the areas granted (members, statements, records, rules,
  disputes), read-only.
- **Settings**: feature flags (on for everyone, or per chama by prefix; loans stay off
  until the lending opinion), bank feeds (add a Jenga feed with encrypted credentials,
  "Pull now"), M-Pesa channels, licensed partners.
- **Audit log**: every staff action and every opening of a chama's records, with the
  chain check.

## Rules it keeps

- Every change and every opening of records needs a **reason and a ticket number**, written
  to the chained audit log. Without them nothing is saved.
- Chama records are **never changed** here. Staff read, and only with the chama's grant.
- The Django staff site (`/staff/`) stays for the few things not yet in the console
  (adding a Daraja channel and registering it with Safaricom). It uses the same sign-in.

## Not yet

Maker and checker (a second staff member approving sensitive actions such as suspending a
chama), ID-check review (with the KYC system), support comments visible to officials, and
weekly access reviews with alerts.
