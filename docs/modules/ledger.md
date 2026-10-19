# ledger

The chama's list of every money fact, written once and never edited. The full flow is in
[Writing to the ledger](../flows/ledger-append.md).

## Tables

| Table | What it holds | Changes allowed |
| --- | --- | --- |
| `ledger_entry` | One entry: chama, seq, type, member code, amount in cents, period, rule refs, decision, source, what it reverses, fingerprint, receipt code | Insert only |
| `ledger_chain_head` | Latest seq and fingerprint per chama | Updated on every append, never deleted |
| `ledger_entry_signature` | Platform signature per entry | Insert only |

## Rules for code that uses it

- `services.append()` is the only way to write an entry. No other code inserts into these
  tables.
- Amounts are integer cents, signed (a correction is negative).
- `seal` entries carry no money: each one records a statement PDF we issued (its
  fingerprint and file hash), so it cannot be re-issued with other figures. Positions and
  statements ignore them. See [Member and chama statements](../flows/member-statements.md).
- Every foreign key into or out of these tables is `PROTECT`. A test fails the build if
  one is ever `CASCADE` or `SET_NULL`.
- Changing anything in `hashing.py` breaks every existing chain. Bump `HASH_VERSION` and
  regenerate the vectors (`python scripts/make_vectors.py`).

## Platform signing (`signing.py`)

ECDSA P-256 over the 32-byte fingerprint, as a pre-hashed SHA-256 digest (what AWS KMS
calls `MessageType=DIGEST`, `ECDSA_SHA_256`). Signatures are DER. `PLATFORM_SIGNER=local`
uses a key from the environment and is for development only; production uses `kms`.

## Jobs

| Task | Queue | When |
| --- | --- | --- |
| `sign_entry` | `signing` | After each append, via the outbox |
| `sign_pending` | `signing` | Every minute |
| `verify_chains` | `bulk` | Nightly at 01:00 EAT |
