# Changing the rules by vote (5.10, rule 10.1)

The rules a chama adopted are not fixed forever, but they never change quietly. A change
goes through four steps, and every member sees each one.

## 1. Propose

An official sends the full new settings and a reason:
`POST /chamas/{id}/rule-proposals/` with `{rules, reason}` (needs an `Idempotency-Key`).

The settings are checked exactly like the rules at setup, so they cannot contradict each
other. Refused with:
- `rules_invalid`: the same error list as the rules editor (`errors: [{path, code}]`).
- `no_change`: these are the rules already in force.
- `proposal_open`: another open proposal changes the same section (for example both touch
  `contributions`). It waits until that one is decided, so members never vote on two
  conflicting versions at once.
- `cooling_off`: exactly the same change was turned down within `cooling_days` (default 30).
- `not_allowed`: only officials propose. Members raise ideas at meetings.

The proposal stores which settings change (`changed`, as `section/key` paths) and the
rule-book clauses before and after, in English and Kiswahili, so members read the change
in words, not JSON.

## 2. Notice

Members have `notice_days` (default 14) to read it before voting starts. The official who
proposed it can withdraw it during notice (`POST /rule-proposals/{id}/withdraw/`). With
`notice_days: 0` the vote opens straight away.

## 3. Vote

When notice ends (the hourly task `advance_rule_proposals`, or any read of the proposals),
a members' vote opens: "Change the rules: {reason}?", every active member eligible, open
for `vote_days` (default 7). It works like every other vote (phone key, SMS code, YES/NO by
SMS); see [votes](votes.md).

The thresholds are the rule-change ones, not the general vote ones: by default half the
members must vote and **more than 66%** of the votes must be yes.

**Changing the rule-change rule itself** (the `rule_changes` section) uses the stricter of
the current and the proposed thresholds. A chama cannot lower the bar by a vote that
already uses the lower bar.

## 4. Start date

- **Passed**: a new rule version is created as `scheduled`, starting on the **1st of next
  month**, and a `rule_version_adopted` entry goes on the ledger (version number, rules
  hash, start date, what changed, the vote's yes and no). Nothing applies backwards:
  payments and fines up to the start date follow the old version.
- On the start date the hourly task puts it in force; the old version becomes
  `superseded` and the proposal `in_force`.
- **Failed**: `rejected`, and the cooling-off period starts.
- **Too few voted**: `lapsed`, which also starts the cooling-off period.

## Why it works this way

- **Full settings, not a patch**: the checker sees the whole result, so a change that is
  fine alone but clashes with another setting is caught.
- **Notice before voting**: members who rarely open the app still hear about it in time,
  through meetings and SMS invites when the vote opens.
- **1st of next month**: contributions and fines are monthly, so a change mid-month would
  split one period under two rule sets.
- **Proposals are append-only** except `state`, `vote_id` and `new_version_id`: a
  proposal's settings and reason can never be edited after members have read them.

## Protections (2026-10-20)

Some of this changed so that a few people cannot use the rules against one member. See
[protections](protections.md).
