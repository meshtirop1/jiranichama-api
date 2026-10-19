# JiraniChama API docs

Plain-English notes on how the backend works and why. Every pull request updates the
pages it touches: **flows** say what happens step by step, **modules** say what each app
owns.

## Flows

- [Writing to the ledger](flows/ledger-append.md): how a money fact is chained, signed and checked.
- [Phone sign-in](flows/phone-sign-in.md): SMS code, PIN and tokens.
- [Staff console](flows/staff-console.md): two-factor login and the audit log.
- [Setting up a chama](flows/chama-setup.md): create, invite, adopt the rules, go live.
- [Bank statements](flows/statements.md): the bank's email or a two-official upload, and the checks.
- [Payments](flows/payments.md): matching, the rules engine, assign and "not me", late fines.
- [Opening balances and cash at meetings](flows/opening-and-cash.md).
- [Member and chama statements](flows/member-statements.md): figures, sealed PDFs, the public check.
- [Money out](flows/money-out.md): proposals, approvals, matching debits, unapproved-debit alerts.
- [Merry-go-round rotation](flows/rotation.md): the checkable draw, whose turn is next, arrears.
- [Phone signing keys](flows/device-keys.md): fingerprint approvals, attestation, nonces.
- [Daily reconciliation](flows/reconciliation.md): bank vs records each morning, gaps, breach after 7 days.
- [Members' votes](flows/votes.md): votes on large payouts, by phone key or SMS code, secret if the rules say.
- [Overdue bank statements](flows/statement-reminders.md): treasurer, then officials, then every member.
- [M-Pesa paybill](flows/paybill.md): Daraja C2B, matched the same minute, never counted twice.
- [SMS commands](flows/sms-commands.md): BAL, DUE, FINES, RULES, VERIFY and YES/NO votes from basic phones.
- [Public anchoring](flows/anchoring.md): nightly Merkle root of every chain, OpenTimestamps, off-site email.
- [Changing the rules](flows/rule-changes.md): propose, 14 days' notice, members' vote, new version from the 1st of next month.

## Modules

- [core](modules/core.md): canonical JSON, encryption, outbox, errors.
- [ledger](modules/ledger.md)
- [accounts](modules/accounts.md)
- [notify](modules/notify.md)
- [flags](modules/flags.md)
- [chamas](modules/chamas.md)
- [staff](modules/staff.md)
- [rules](modules/rules.md): schema, templates, rule book, allocation engine.
- [devices](modules/devices.md): SMS signing codes and phone signing keys.
- [feeds](modules/feeds.md): statements, parsers, bank transactions.
- [reconciliation](modules/reconciliation.md)
- [contributions and fines](modules/contributions-and-fines.md)
- [documents](modules/documents.md): statements, PDFs, the check page.
- [payouts](modules/payouts.md): money out.
- [votes](modules/votes.md): members' votes.

## Operations

- [Database roles](operations/database-roles.md)

## Where we are

Phase 0 (foundations) is done. Phase 1 (records core) is in progress: chama setup and
adoption, rules engine v1, statements by bank email or two-official upload, matching,
contributions and late fines are built. Opening balances, cash at meetings, and member
and chama statements (sealed PDFs with a public check) are built too. Still to come in
Phase 1: nothing beyond polish. Unreadable statement lines can be typed in by an official and confirmed by another, and the app edits every rule setting (rule errors carry stable codes). Phase 2 has started: money out (proposals, approvals by SMS code, debits matched back, unapproved-debit alerts) the merry-go-round rotation, approvals signed with the phone's key (fingerprint) for officials with the app, daily reconciliation of the bank against the records, members' votes on large payouts, overdue-statement reminders, M-Pesa paybill notifications, SMS commands, votes, receipts and joining for basic phones, and nightly public anchoring. Phase 2 is complete. Phase 3 (governance and loans) has started: rule changes by members' vote are built. Still to come: meetings and minutes, loans with guarantors, fine waivers, disputes, elections, leaving and death. Then Phase 4: USSD and bank feeds.

## Not in Phase 0, on purpose

- `Idempotency-Key` is required on writes that create money facts or records (creating a
  chama, uploading a statement, assigning a payment, "not me"). Sign-in does not need it:
  a repeated code request just sends another code.
- KMS envelope encryption for personal data. Phase 0 uses Fernet keys from the
  environment behind the same `encrypt` / `decrypt` calls, so callers will not change.
