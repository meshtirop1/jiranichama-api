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
| `models.py` | `RuleVersion`: settings frozen at adoption; only `state` can change. `RuleProposal`: a change waiting for notice and a vote |
| `proposals.py` | Rule changes by vote: propose, withdraw, `advance` (notice ends, vote opens; scheduled versions start), `on_vote_closed` |
| `views.py`, `tasks.py` | The rule-proposal endpoints and the hourly `advance_rule_proposals` |
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
meetings and other fines arrive in Phase 3 as schema v2. The
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
`tiers_too_many_officials`, `fine_needs_amount`, `fine_cap_below_fine`, `waivers_too_many_officials`, `disputes_too_many_officials`. `message` stays as
the English explanation for logs and staff.

## Rotation (schema v1, optional section)

`rotation: {order: fixed|draw, arrears: skip|deduct|block}`. Only merry-go-round templates
include it; the rule book adds clauses 8.1 and 8.2 when present.

## Votes (schema v1, optional section)

`votes: {quorum_pct: 10-100, pass_pct: 50-90, secret: bool}`, in every template as
`{50, 50, false}`. Missing means the same defaults. The rule book's clause 9.1 says it.

## Rule changes (schema v1, optional section)

`rule_changes: {notice_days: 0-60, vote_days: 1-30, quorum_pct: 25-100, pass_pct: 50-90,
cooling_days: 0-365}`, in every template as `{14, 7, 50, 66, 30}`. The rule book's clause
10.1 says it. The flow is in [rule changes](../flows/rule-changes.md). A version's states:
`scheduled` (adopted by vote, waiting for its 1st of the month), `in_force`, `superseded`.

## Meetings (schema v1, optional section)

`meetings: {absence_fine_cents, late_fine_cents, late_after_minutes: 0-180,
apology_excuses: bool, objection_days: 1-30}`, in every template as `{0, 0, 15, true, 7}`.
Clauses 11.1 (attendance, absence fine), 11.2 (lateness) and 11.3 (minutes).

## Loans (schema v1, optional section)

`loans: {min_months, savings_multiple_pct, interest: flat|reducing, monthly_rate_bp,
max_term_months, guarantors, grace_days, late_penalty_cents, default_after_missed,
contribution_first}`. Only the table-banking template has it, as `{3, 300, flat, 100, 12,
2, 3, KES 200, 2, true}`. Without it a chama has no loans. Clauses 12.1-12.4. See
[loans](../flows/loans.md).

## Waivers (schema v1, optional section)

`waivers: {officials: 1-5, member_vote: bool}`, in every template as `{2, false}`. Clause
3.5. `officials` may not exceed the number of officials minus one.

## Disputes (schema v1, optional section)

`disputes: {decided_by: officials|vote, officials: 1-5, response_hours: 24-336,
allow_officials_only: bool}`, in every template as `{officials, 2, 72, true}`. Clause 14.1.
With `officials`, `officials` may not exceed the number of officials minus one.

## Elections (schema v1, optional section)

`elections: {term_months: 6-60, vote_days: 1-30}`, in every template as `{24, 7}`.
Clause 15.1.

## Leaving and death (schema v1, optional section)

`exit: {notice_days: 0-180, deduction_pct: 0-50, death_benefit_cents}`, in every template
as `{30, 0, 0}`. Clause 16.1.

## Protections (2026-10-20)

Some of this changed so that a few people cannot use the rules against one member. See
[protections](../flows/protections.md).

## Country rule packs (2026-10-21)

`packs.py` holds each country's pack: size limits, rule settings the law forbids (checked by `validation.check(rules, country)`), warnings and duties. `templates.fit_country` makes starting rules lawful, and `compliance(chama)` feeds `/chamas/{id}/country-rules/`. See [countries](../flows/countries.md).
