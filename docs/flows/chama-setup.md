# Setting up a chama

States: `setup` → `awaiting_officials` → `awaiting_adoption` → `active`, plus `expired`.
An active chama starts in **records-only** mode; payouts, withdrawals and loans unlock in
Phase 2 once every official has passed the ID check and registered a signing key.

## Steps

1. **Create.** `POST /chamas/` (with an `Idempotency-Key`). The founder picks the type and
   their own role. The chama gets a member-code prefix (`JC-4821`), the founder gets the
   first member code, the type's rule template is copied into the draft, and the chama
   gets its own **statement inbox** address. Setup expires after 14 days.
2. **Bank account.** `PUT /chamas/{id}/bank-account/`: bank, account name and number, how
   members pay, and how statements arrive (bank email or upload). The number is stored
   encrypted; only the last four digits are shown back.
3. **Rules.** `GET /chamas/{id}/rules/` shows the draft settings and the rule book in
   English and Kiswahili. `PUT` replaces the settings; contradictions are refused with
   every problem listed (see [rules](../modules/rules.md)).
4. **Invite.** `POST /chamas/{id}/invites/` makes a 6-letter code. Officials are always
   invited by phone, and only that phone can use the invite. Invites last 14 days.
5. **Submit.** `POST /chamas/{id}/submit/` needs a bank account and valid rules. The chama
   waits for officials until every office in the rules is held, then opens adoption.
   Everyone in the chama at that point is a **founding member**.
6. **Join.** `GET /invites/{code}/` shows the chama and its rule book.
   `POST /invites/{code}/` sends an SMS code that names the chama: "…to JOIN Umoja Women
   Group and accept its rules". Posting the code with its `challenge_id` joins.
7. **Adopt.** Each founding member confirms the rule book the same way
   (`POST /chamas/{id}/adopt/`). When all officials and the share of founding members the
   rules set (default 51%) have confirmed, the chama becomes active.
8. **Active.** The rules are frozen into rule version 1, and the ledger gets a
   `rule_version_adopted` entry and a `membership` entry per founding member. Later
   members who join get their own `membership` entry.

## Why it works this way

- **Officials are invited by phone.** An office cannot be taken by whoever finds the code.
- **SMS codes name the action and are bound to what was shown.** If the rules change
  after a code was sent, the code no longer works (`signing_content_changed`).
- **Nothing is on the ledger until the chama is active.** Setup can be abandoned without
  leaving a history behind.

## Not yet

Opening balances for chamas moving from a notebook, entry fees and probation, and
membership votes arrive in later Phase 1 and Phase 3 pull requests.
