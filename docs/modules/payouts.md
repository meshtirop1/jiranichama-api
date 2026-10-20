# payouts

Money out: proposals, approvals, matching debits, unapproved debits. See
[Money out](../flows/money-out.md).

- `models.py`: `Proposal` (only state, payment and matching fields may change; the
  database enforces it), `Approval` (insert only; one answer per official), `UnapprovedDebit`
  (only its resolution fields change), `MoneyOutConsent` (insert only).
- `services.py`: `start_enable`/`enable` (two officials switch money out on), `propose`
  (rules check: arrears, reconciled balance less promised money, approval level),
  `start_approve`/`approve` (SMS code over a hash of exactly what is paid), `decline`,
  `cancel`, `expire_due`, `mark_paid`, `process_debit` (called by feeds ingest for every
  debit), `alert_members` (after commit), `link`, `explain`.
- `tasks.py`: `expire_proposals`, hourly.
- `views.py`: the endpoints. `can_answer` on a proposal tells the app whether the person
  asking may approve or decline it now.
- `rotation.py`: merry-go-round cycles (`Rotation`, `RotationTurn`): `start` (fixed or a
  draw seeded by the chain fingerprint), `draw_order`, `propose_next` (fills in the payout,
  applies rule 8.2), `on_paid` (called when a payout is matched).
  See [Merry-go-round rotation](../flows/rotation.md).

## Protections (2026-10-20)

Some of this changed so that a few people cannot use the rules against one member. See
[protections](../flows/protections.md).
