# shareout

The year-end share-out. Flow: [year-end share-out](../flows/shareout.md).

- `models.ShareOut` (figures frozen when it goes to the vote; only the state and what it led
  to change) and `ShareOutLine` (one member's part; only its payment is filled in).
- `services.compute`: the figures from the ledger. `draft`, `submit` (the members' vote),
  `on_vote_closed` → `_approve` (fines and loans settled in the books, then
  `propose_payments`), `paid_extra` / `on_paid` (hooked into `payouts._write_payout`).
- Savings given back are subtracted in `contributions.projections.position`
  (`shared_out_cents`), so exits, loans and the next share-out see the right savings.
- Fines and loans paid from a share have source `shareout`, which the daily bank check
  leaves out (`reconciliation.daily.INSIDE_BOOKS`).
