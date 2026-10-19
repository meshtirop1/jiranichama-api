# elections

Elections and role changes (5.12). The flow is in [elections](../flows/elections.md).

| File | What it does |
| --- | --- |
| `models.py` | `Election` (append-only except its state, vote, role entry and bank-mandate confirmation) |
| `services.py` | `nominate` (opens the vote; installs an acting official at once), `on_vote_closed`, `ready` / `try_install` / `install_ready` (the role starts when the phone key is active), `confirm_mandate`, `needs_election` |
| `views.py`, `tasks.py` | The election endpoints; `install_elected` hourly |

Rules section `elections: {term_months: 6-60, vote_days: 1-30}`, clause 15.1. Other
apps call in: `devices.changes` installs a waiting winner when their key becomes active,
and `payouts._rules_check` refuses money out with `election_needed` when too few
officials remain to approve it.
