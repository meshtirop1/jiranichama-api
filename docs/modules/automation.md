# automation

Makes the chama run itself (2026-10-21). Flow: [the chama runs itself](../flows/automation.md).

- `models.Notice`: one message to one person, unique by key, so every reminder goes once.
  `MonthlyPack`: a chama's month done.
- `notices.notify`: write the notice; send the SMS when the person is away from the app
  (`away`), always, or never.
- `chasers.py`: the hourly chasing (approvals, votes, paid-not-seen, cash, statements,
  minutes, the automatic dispute) and the treasurer's morning count of unmatched payments.
- `reminders.py`: payment reminders (rule 3.7), `owed()` (what to pay now) and
  `how_to_pay()`.
- `monthly.py`: the pack on the 1st, and `propose_bill` (one tap for the treasurer).
- `views.py`: `/me/notices/`, `/chamas/{id}/pay/`, `/pay-requests/{id}/`,
  `/bills/{id}/propose/`.
- `tasks.py`: `chase_hourly` (:25), `morning` (08:00), `monthly_pack` (1st, 05:00).

Pay now itself lives in `feeds/stk.py` (next to the Daraja C2B code), and smart matching
in `reconciliation/matching.py`.
