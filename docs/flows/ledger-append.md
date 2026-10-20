# Writing to the ledger

Every money fact in a chama (a contribution, a fine, a payout, a correction) is one
**ledger entry**. Entries are never edited or deleted. A mistake is fixed by a new
correction entry that points at the one it reverses.

## Why it is built this way

The promise is that nobody can quietly change a chama's history: not the treasurer, not
the officials, not JiraniChama staff. Three things make that true:

1. **The database refuses changes.** Triggers on `ledger_entry`, `ledger_entry_signature`
   and `staff_audit_log` raise an error on any UPDATE or DELETE. In production the app's
   database role also has no UPDATE or DELETE grant on them.
2. **Each entry's fingerprint includes the one before it.** Change one entry and every
   fingerprint after it stops matching. Every member's phone recomputes the chain.
3. **The platform signs each fingerprint.** A phone checks the signature against our
   published key, so even a forged chain with matching fingerprints is caught.

## Step by step (`apps.ledger.services.append`)

1. The caller (a contribution, a fine job, a payout) calls `append()` inside its own
   database transaction.
2. `append()` locks the chama's **chain head** row (`SELECT … FOR UPDATE`). This makes
   writes in one chama happen strictly one after another. It is a row lock, not a session
   lock, because PgBouncer runs in transaction mode.
3. It builds the entry document: `seq` = head + 1, `prev_hash` = the head's hash, the time
   cut to milliseconds, and every hashed field (null when empty).
4. The fingerprint is SHA-256 of the canonical JSON of that document
   ([canonical JSON](../modules/core.md)). The receipt code a member types after `VERIFY`
   is the first 8 Base32 characters of it.
5. The entry is inserted, the head moves on, and an outbox event
   `ledger.entry_appended` is written, all in the same transaction.
6. **After commit**, the outbox worker hands the event to the signing worker, which signs
   the fingerprint with the platform key (KMS in production) and stores an
   `EntrySignature`. No network call happens while the head is locked, so a slow KMS call
   never holds up other writes in that chama.
7. Every minute a sweep signs any entry whose event was missed.

## Checking a chain (`verify_chain`)

Nightly, and on demand, every chain is recomputed: each `seq` follows the last, each
`prev_hash` links to the hash before, each stored hash matches its recomputed contents,
each platform signature is valid, and the head agrees with the last entry. A problem is
reported at its position, for example `seq 2: contents do not match its hash`. Entries
still unsigned after 10 minutes are reported to staff.

## Shared vectors

`vectors/ledger_chain.json` holds a small chain with every document, canonical string,
hash and receipt code. The Python tests and the Flutter app's tests both recompute it, so
the phone and the server can never disagree on what a fingerprint is. CI fails if the
vectors are stale. Changing the hashing rules means bumping `HASH_VERSION`.

## What can go wrong

- **Two writers append to the same chama at once.** The head lock makes the second wait.
- **The first entry in a chama.** The head is created on demand; if two writers race to
  create it, one loses the insert and locks the other's row.
- **KMS is down.** Entries still commit (members see them at once); they are marked
  signed when the sweep succeeds.
- **A database superuser bypasses the triggers and edits a row.** `verify_chain` reports
  the position, and from Phase 2 the nightly public anchor proves what the chain was.

## Trust (2026-10-20)

See [trusting the records](trust.md): the platform key chain, member countersignatures, the public proof file and the public lot for tied elections.
