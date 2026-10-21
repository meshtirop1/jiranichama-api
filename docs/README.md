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
- [Payments](flows/payments.md): matching, the rules engine, assign and "not me", late fines, and when fines stop.
- [Countries](flows/countries.md): nine countries (Kenya, Uganda, Tanzania open), phones, currencies, banks, languages.
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
- [Numbers for running the chama](flows/analytics.md): month by month, who is behind, attendance, loans, money.
- [Admin web console](flows/admin-console.md): staff dashboard, queues, chamas, flags, feeds, partners, audit log.
- [Support access](flows/support-access.md): officials let support read some records for a while; every member is told.
- [Referrals](flows/referrals.md): licensed partners only, with the members' recorded consent.
- [Investments](flows/investments.md): land, shares, funds; valuations confirmed by a second official; income through the bank.
- [Bank feeds](flows/bank-feeds.md): Equity Jenga balance and lines, matched like statements, never counted twice.
- [USSD menu](flows/ussd.md): read-only menu for any phone: balance, due, loan, fines, rules, verify.
- [Public anchoring](flows/anchoring.md): nightly Merkle root of every chain, OpenTimestamps, off-site email.
- [Leaving and death](flows/exits.md): what comes back, notice, settlement from savings, next of kin.
- [Elections](flows/elections.md): members elect officials; the role starts with the phone key; acting officials; bank mandate; money out waits when too few officials remain.
- [Disputes](flows/disputes.md): raise a problem on any record, 72 hours to respond, uninvolved officials or a vote settle it, the outcome written as entries.
- [Fine waivers](flows/fine-waivers.md): ask with a reason, officials decide (never their own), fine_waived on the ledger.
- [Loans](flows/loans.md): behind the loans flag; quote, guarantors by SMS code, approval as money out, interest on due dates, arrears and default.
- [Meetings](flows/meetings.md): schedule, reminders, apologies, QR check-in, absence and lateness fines, minutes sealed after a week.
- [Changing the rules](flows/rule-changes.md): propose, 14 days' notice, members' vote, new version from the 1st of next month.
- [Protections](flows/protections.md): five members minimum, small-group unanimity, a second official to confirm a death, "I am alive", withdrawals the members must accept, disputes that take money.
- [Registering a chama](flows/registration.md): each country's law, office, steps and fee; the papers JiraniChama fills in; the officials' checklist and the reminder.
- [Trusting the records](flows/trust.md): the endorsed platform key chain, members countersigning the chain, the public .ots proof, and the public lot for elections tied twice.
- [Bank protections](flows/bank-protections.md): the bank mandate confirmed by a second official, statements sealed on the chain, no skipped periods, uploads that must match the bank feed, a harder M-Pesa callback.
- [ID checks and privacy](flows/identity.md): Smile ID checks paid by the chama (off until configured), one person one ID, rule P.3, encrypted next of kin, no full numbers in vote SMS, staff need a support grant.
- [JiraniChama's monthly bill](flows/billing.md): USD 0.10 per active member, first 3 months and chamas of 5 or fewer free, one bill per chama, ID checks added.

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
- [meetings](modules/meetings.md): meetings, attendance, minutes.
- [loans](modules/loans.md): loans and guarantees (flag off).
- [investments](modules/investments.md): holdings, valuations, income.
- reports (`apps/reports/analytics.py`): the numbers for running the chama.
- [disputes](modules/disputes.md): disputes, the evidence pack.
- [elections](modules/elections.md): elections, acting officials, role changes.
- [exits](modules/exits.md): leaving and deaths.
- [registration](modules/registration.md): government registration guides, filled-in papers, checklist.
- [identity](modules/identity.md): ID checks through Smile ID, one person one ID.
- [billing](modules/billing.md): the monthly bill.

## Operations

- [Database roles](operations/database-roles.md)
- [Efficiency](operations/efficiency.md): gzip, query counts per endpoint, what is still slow

## Where we are

Phase 0 (foundations) is done. Phase 1 (records core) is in progress: chama setup and
adoption, rules engine v1, statements by bank email or two-official upload, matching,
contributions and late fines are built. Opening balances, cash at meetings, and member
and chama statements (sealed PDFs with a public check) are built too. Still to come in
Phase 1: nothing beyond polish. Unreadable statement lines can be typed in by an official and confirmed by another, and the app edits every rule setting (rule errors carry stable codes). Phase 2 has started: money out (proposals, approvals by SMS code, debits matched back, unapproved-debit alerts) the merry-go-round rotation, approvals signed with the phone's key (fingerprint) for officials with the app, daily reconciliation of the bank against the records, members' votes on large payouts, overdue-statement reminders, M-Pesa paybill notifications, SMS commands, votes, receipts and joining for basic phones, and nightly public anchoring. Phase 2 is complete. Phase 3 (governance and loans) is complete: rule changes by members' vote, meetings with attendance, meeting fines and minutes, loans with guarantors (behind the loans flag until the lending opinion), fine waivers, disputes, phone changes, elections, and leaving and deaths. Phase 4 (basic phones and bank feeds) is complete: the USSD menu, the LOAN SMS command, and bank feeds through Equity's Jenga API (to be checked in UAT). Phase 5 (growth) has investments with valuations, numbers for running the chama, and referrals to licensed partners with consent. Still with the owner: partner agreements, more languages (translators) and more bank parsers (real statement files).

## Not in Phase 0, on purpose

- `Idempotency-Key` is required on writes that create money facts or records (creating a
  chama, uploading a statement, assigning a payment, "not me"). Sign-in does not need it:
  a repeated code request just sends another code.
- KMS envelope encryption for personal data. Phase 0 uses Fernet keys from the
  environment behind the same `encrypt` / `decrypt` calls, so callers will not change.
