# From a bank payment to a recorded contribution

A payment is counted once, against one member, under the rules in force on the day the
bank says it was paid.

## Matching

1. **Member code** in the narration or reference (`JC-4821-017`, also `JC 4821 017`). A
   code from another chama is never recorded here: that money is not in this account.
2. **Phone number** in the narration (or from Daraja, Phase 2), if it belongs to exactly
   one member of this chama.
3. Otherwise the payment waits in the **unmatched queue** (`GET /chamas/{id}/unmatched/`).

## Recording (`reconciliation.services.record`)

The rules engine splits the payment ([rules](../modules/rules.md)):

- in the order the rules set (default: unpaid past months, then fines, then this month);
- then into months ahead; anything left is a credit;
- each slice says whether it was on time or late, judged by the **bank date**.

Each slice is a ledger entry: `contribution` (with its period) or `fine_paid`.

If a late fine was raised before an on-time payment reached us (a statement uploaded a
week later), and the payment completes that month on time, the fine is **reversed** by a
new entry, never deleted.

## The treasurer assigns

`POST /bank-transactions/{id}/assign/` with the member and a reason ("paid from her
husband's phone"). Only the treasurer can. The assignment is on the ledger for everyone.

## "This was not me"

`POST /bank-transactions/{id}/not-me/` by the member it was assigned to. Every entry the
assignment produced is reversed by a new entry, fines go back to where they stood, and
the payment returns to the queue. Each assignment's entries carry its match id, so a
second assign-and-reject never reverses the first reversal. Phase 3 also opens a dispute.

## Late fines

Every hour (`fines.tasks.raise_due_fines`), each unpaid period whose grace ended gets its
late fine once: one per member, rule and period, so running the job twice is harmless.
