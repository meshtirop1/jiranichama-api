# investments

Investments (4.10). The flow is in [investments](../flows/investments.md).

| File | What it does |
| --- | --- |
| `models.py` | `Investment` (append-only except state and sale date), `Valuation` (append-only except its confirmation) |
| `services.py` | `record`, `value`, `confirm` (a different official), `add_income` (treasurer assigns an unmatched credit), `latest_value`, `income_cents`, `portfolio` |
| `views.py` | The endpoints |

Ledger types: `investment_recorded` and `valuation` (not bank money), `investment_income`
(bank money, in the reconciliation's `BANK_TYPES`).
