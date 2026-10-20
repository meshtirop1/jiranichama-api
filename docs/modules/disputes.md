# disputes

Disputes (5.14). The flow is in [disputes](../flows/disputes.md).

| File | What it does |
| --- | --- |
| `models.py` | `Dispute` (append-only except its state, round, proposed outcome, vote and ledger entry), `DisputeMessage` (append-only: responses, notes, agree, disagree, per round) |
| `services.py` | `raise_dispute` (works out who is involved from the subject), `can_see` / `visible`, `open_on_proposal` (pauses money out), `respond`, `withdraw`, `escalate`, `deciders`, `propose_outcome`, `start_agree` / `agree`, `disagree`, `on_vote_closed`, `_apply` (writes the entries), `evidence` |
| `views.py` | The dispute endpoints |

Rules section `disputes: {decided_by: officials|vote, officials, response_hours,
allow_officials_only}`, clause 14.1. Ledger type `dispute_resolved`. A `correction` entry
now counts in a member's contributions (as credit when it has no period).

Other apps call in: `reconciliation.not_me` raises a `payment` dispute, and
`payouts` refuses approvals and "paid" while `open_on_proposal`.

## Protections (2026-10-20)

Some of this changed so that a few people cannot use the rules against one member. See
[protections](../flows/protections.md).
