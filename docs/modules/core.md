# core

Shared building blocks. Nothing here knows about chamas.

## Canonical JSON (`canonical.py`)

Before anything is hashed or signed it is turned into one exact string, so the server
and every phone get the same bytes. We follow RFC 8785 (JCS) for a small set of values:
objects with string keys, arrays, strings, true/false/null, and whole numbers up to
2^53 − 1. Keys are sorted by UTF-16 code units, as the RFC says.

**Floats are refused.** Money is always whole cents, and leaving floats out removes the
hardest part of RFC 8785 from both the Python and Dart versions.

Times are written as UTC with milliseconds and a `Z` (`2026-03-04T09:15:30.123Z`), and
stored truncated to milliseconds, so what is stored is exactly what was hashed.

The Dart app has a twin of this module. Both are held to `vectors/canonical_json.json`.

## Encryption and keyed hashes (`crypto.py`)

- `encrypt` / `decrypt`: Fernet with a list of keys (newest first) so keys can rotate.
  Production moves to KMS envelope encryption behind the same two calls.
- `keyed_hash(purpose, value)`: HMAC-SHA256, separated by purpose, so a phone hash can
  never be reused as, say, an ID-number hash.

## Outbox (`outbox.py`, `tasks.py`)

`emit(topic, payload)` writes an event row in the caller's transaction. After commit, and
every 30 seconds as a sweep, `publish_outbox` hands events to their handlers. A crash
either keeps both the change and its event or neither. Handlers must be safe to run twice.

## Errors (`problems.py`)

All API errors are RFC 9457 problem documents (`application/problem+json`) with a stable
`code`. Raise `Problem("code", "Plain sentence for the user.")` for a known refusal.

## Append-only guard (`guards.py`)

Creates `jirani_forbid_change()` and attaches it as a trigger to the tables that must
never change. TRUNCATE is not trapped, so tests can reset the database; production
revokes TRUNCATE from the app instead ([database roles](../operations/database-roles.md)).

## Mostly append-only tables (`append_only_except`)

Some history rows have one status that must still move (a statement's `state`, a bank
line's `state`, a rule version's `state`). `jirani_forbid_change_except(columns)` refuses
DELETE and any UPDATE that changes anything other than those columns.

## Idempotency (`idempotency.py`)

`@idempotent()` on a write stores its first successful response under (user,
`Idempotency-Key`). A retry with the same key and body gets that response back, marked
`Idempotent-Replayed: true`; the same key with a different body is refused (422); a
missing key is refused (428).

## SMS-confirmed actions (`two_step.py`)

`POST` without a code sends one and answers 202 with `challenge_id`; the same `POST` with
`challenge_id` and `code` does the action.
