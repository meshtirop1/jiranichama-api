# Opening balances and cash at meetings

## Opening balances (5.1 step 10)

A chama moving from a notebook brings in what each member already had.

1. **Draft.** The treasurer enters each member's savings and unpaid fines
   (`PUT /chamas/{id}/opening-balances/`). The draft can be changed until it is confirmed;
   nothing is on the ledger yet.
2. **Confirm.** A **different** official confirms with an SMS code that names the number
   of members and the total savings (`POST /chamas/{id}/opening-balances/confirm/`, two
   steps). The treasurer cannot confirm their own figures.
3. **On the ledger.** Each member gets an `opening_balance` entry for their savings and,
   if they owed fines, a `fine_raised` entry with rule reference `opening`. Both entries
   name the two officials who signed.
4. **Objections.** For 14 days each member can object to their own line
   (`POST /opening-balance-lines/{id}/object/`). The ledger does not change; every member
   sees the objection. Phase 3 turns objections into disputes.

Opening savings count in a member's total contributions (`opening_cents` in the
position). Opening fines are paid like any fine, in the order the rules set. Unlike late
fines they are never reversed as "raised in error", because they were not raised by a
due date.

## Cash at meetings (5.4)

Cash counts only when the member has confirmed handing it over **and** it is in the bank.

1. **Record.** The treasurer records the cash: member, amount, date
   (`POST /chamas/{id}/cash-receipts/`). Nothing is on the ledger yet.
2. **The member confirms** with an SMS code naming the amount and date
   (`POST /cash-receipts/{id}/confirm/`). This writes a `contribution_pending_cash` entry;
   the member's position shows it as pending cash. If they did not hand it over, they
   decline (`/decline/`): nothing counts and officials see why.
3. **Banked.** When the deposit appears on a statement, the treasurer links the unmatched
   deposit to the confirmed receipts it carries (`POST /bank-transactions/{id}/bank-cash/`).
   The receipts must add up to the deposit exactly.
4. **Judged by the rules.** Each member's share goes through the same rules engine as a
   bank payment. It counts from the day the cash was handed over if it reached the bank
   within `cash_banking_days` (default 3, rule 3.4); otherwise from the bank date. Each
   receipt also gets a `cash_banked` entry linking it to its pending entry.

A confirmed receipt not banked within the window is shown as **overdue**. Phase 2 turns
that into an alert to every member after 7 days, as the proposal sets out.
