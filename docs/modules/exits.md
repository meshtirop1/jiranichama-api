# exits

Leaving the chama and the death of a member (5.13). The flow is in
[leaving and death](../flows/exits.md).

| File | What it does |
| --- | --- |
| `models.py` | `Exit` (append-only except its state, quote and proposals) |
| `services.py` | `quote`, `start_leave` / `leave` (SMS code), `cancel`, `record_death`, `settle_due` (fines and loan from savings with source `exit`, guarantors called on a shortfall, a `settlement` proposal), `on_paid` |
| `views.py`, `tasks.py` | The endpoints; `settle_exits` daily at 07:45 |

Rules section `exit: {notice_days: 0-180, deduction_pct: 0-50, death_benefit_cents}`,
clause 16.1. `payouts` has the `settlement` kind (proposed by the leaving member or the
official recording a death) and calls `on_paid`; `reconciliation.daily` leaves out
entries with source `exit`. `chamas.access.require_member` lets an `exiting` member keep
reading the chama.

## Protections (2026-10-20)

Some of this changed so that a few people cannot use the rules against one member. See
[protections](../flows/protections.md).
