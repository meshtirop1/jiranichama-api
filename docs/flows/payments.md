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

### Fines stop (rule 3.6, owner decision 2026-10-20)

The owner asked that a member is not fined without end: "we fine until we stop". In
`apps/fines/limits.py`:

- **Only the first N missed months are fined.** N is `fines.stop_after_missed_months`,
  default 3, between 1 and 12. "Missed" means the grace period has ended and the month is
  still not fully paid.
- **After N, no fines of any kind are added:** late fines, meeting fines and loan
  penalties all stop.
- **Each month that was not fined is recorded** as a `FineStop`. That month is never
  fined later, even after the member catches up on older months.
- **The member and the officials get one SMS** the first time fining stops. It says to
  agree a payment plan, a waiver or the exit process. From then on the group's money is
  protected by talking, not by fines that only grow.
- **The member's position shows `fines_stopped`**, and the app shows it on their overview.

The rule book's monthly cap (`fines.late_contribution.monthly_cap_cents`) now really
limits the total of **all** fines raised for one member in one month. Before this change
the code applied it to the late fine alone, where it never did anything.

## From M-Pesa directly

Chamas with their own paybill or till also get each payment from Safaricom within
seconds (Daraja C2B), matched the same way; the later statement line is recorded as a
duplicate of it. See [M-Pesa paybill](paybill.md).

## Receipts (5.3 step 8)

Once a payment is recorded (matched automatically, assigned, from M-Pesa or banked cash),
the member is told once per payment. App users see the entry in the app. Members who have
not opened the app in `RECEIPT_SMS_IF_AWAY_DAYS` days (30), or never, get an SMS:
"JiraniChama: KES 1,000 received for Mar from Wanjiru (JC-4821-017). Umoja Women Group.
Ref 7KQ2-M9. Check: VERIFY 7KQ2M9" (Kiswahili for Kiswahili speakers). The reference is
the payment's first receipt code, which VERIFY checks by SMS. `User.last_app_at` is
noted by the API's authentication at most once an hour. An arrears deduction from a
merry-go-round turn sends no separate receipt. Push notifications for app users come
later.
