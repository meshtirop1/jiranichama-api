# rules

A chama's constitution as settings, checked, enforced and written out in plain words.

| File | What it does |
| --- | --- |
| `schema.py` | JSON Schema v1: contributions, late fines, officials, approvals, membership, adoption, statements, ID-check payer |
| `templates.py` | Starting settings per chama type (merry-go-round, table banking, welfare, investment club) |
| `validation.py` | Schema plus cross-field checks and the platform rules |
| `book.py` | The numbered rule book in English and Kiswahili, generated from the settings |
| `calendar.py` | Monthly periods, due dates, grace ends |
| `engine.py` | `allocate()`: how one payment is split; pure, no database |
| `models.py` | `RuleVersion`: settings frozen at adoption; only `state` can change |
| `golden/allocate_v1.json` | Hand-worked cases the engine must reproduce |

## Platform rules (no chama can switch these off)

- **P.1** The person paid never approves the payment. So no tier may need more officials
  than there are officials minus one.
- **P.2** Statements come from the bank's email, or are uploaded and confirmed by two
  different officials.
- **P.3** Money goes out only after every official passed the ID check (Phase 2).
- **P.4** Records are never changed; mistakes are fixed by visible corrections.

## Cash at meetings

`contributions.cash_banking_days` (default 3, rule 3.4): cash counts from the day it was
handed over if it reaches the bank within that many days, otherwise from the bank date.

## Limits of v1

Monthly contributions only (weekly and fortnightly need a new period format). Loans,
meetings, other fines and rule changes by vote arrive in Phase 3 as schema v2. The
Kiswahili rule book is a first draft for a native speaker to review before the pilot.

## Changing a template or the engine

Add or change a golden case first, by hand, then make the code pass it. A rule version
stores its template name and schema version, so old chamas keep the rules they adopted.

## Errors the app can translate

`validation.check` returns `RuleError(path, message, code)`. `PUT /chamas/{id}/rules/`
sends them as `errors: [{path, code, message}]`. `path` names the setting
(`contributions/due_day`, `approvals/tiers/1/officials`). `code` is stable, so the app shows
the reason in English or Kiswahili. Codes: `out_of_range` (any schema failure),
`tiers_last_unlimited`, `tiers_only_last_unlimited`, `tiers_order`, `tiers_officials_order`,
`tiers_too_many_officials`, `fine_needs_amount`, `fine_cap_below_fine`. `message` stays as
the English explanation for logs and staff.

## Rotation (schema v1, optional section)

`rotation: {order: fixed|draw, arrears: skip|deduct|block}`. Only merry-go-round templates
include it; the rule book adds clauses 8.1 and 8.2 when present.

## Votes (schema v1, optional section)

`votes: {quorum_pct: 10-100, pass_pct: 50-90, secret: bool}`, in every template as
`{50, 50, false}`. Missing means the same defaults. The rule book's clause 9.1 says it.
