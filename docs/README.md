# JiraniChama API docs

Plain-English notes on how the backend works and why. Every pull request updates the
pages it touches: **flows** say what happens step by step, **modules** say what each app
owns.

## Flows

- [Writing to the ledger](flows/ledger-append.md): how a money fact is chained, signed and checked.
- [Phone sign-in](flows/phone-sign-in.md): SMS code, PIN and tokens.
- [Staff console](flows/staff-console.md): two-factor login and the audit log.

## Modules

- [core](modules/core.md): canonical JSON, encryption, outbox, errors.
- [ledger](modules/ledger.md)
- [accounts](modules/accounts.md)
- [notify](modules/notify.md)
- [flags](modules/flags.md)
- [chamas](modules/chamas.md)
- [staff](modules/staff.md)

## Operations

- [Database roles](operations/database-roles.md)

## Where we are

Phase 0 (foundations) of the proposal. Not built yet, in plan order: Phase 1 records core
(chamas, rules engine, statements, matching), Phase 2 money out (signing keys, proposals,
reconciliation, ID checks), Phase 3 governance and loans, Phase 4 basic phones.

## Not in Phase 0, on purpose

- `Idempotency-Key` handling on writes arrives with the first money-writing endpoints
  (Phase 1). Sign-in does not need it: a repeated code request just sends another code.
- KMS envelope encryption for personal data. Phase 0 uses Fernet keys from the
  environment behind the same `encrypt` / `decrypt` calls, so callers will not change.
