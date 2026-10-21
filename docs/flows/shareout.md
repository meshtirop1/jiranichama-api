# Year-end share-out (2026-10-21, rule 13.1)

At the end of a cycle many chamas give each member their savings back and a share of what
the group earned. Table banking groups and VSLAs do this, and Uganda's law asks for it
every year. JiraniChama works the figures out, the members vote on exactly those figures,
and each payment goes through [money out](money-out.md).

## 1. Draft (an official, `POST /chamas/{id}/shareouts/`)

The official chooses:

- the cycle (`period_from`, `period_to`), which must have ended;
- `return_savings`: give savings back, or keep them in the chama;
- `keep_pct`: up to 50% of the earnings kept back as a reserve;
- `weight`: share by **savings**, or by **savings and months** (money saved times the
  months it was held in the cycle, so January's saving counts more than November's).

The rules' optional `shareout` section sets the defaults, and the rule book says so
(13.1). Only one share-out can be open at a time.

The figures come from the ledger:

- **Earned in the cycle.** Fines paid, loan interest, bank interest and investment income,
  less bank charges. Reversed entries are left out.
- **Shared.** The earnings less the reserve, divided by weight. Rounding goes to the
  reserve.
- **Each member's line:**
  - savings given back (when chosen) plus their share;
  - **less their unpaid fines**;
  - **less their open loan**;
  - what is left is paid to them.
- **Bank check.** The total against the last reconciled bank balance; `short_cents` warns
  when the bank cannot cover it yet.

Every member sees the draft (`GET /shareouts/{id}/`). Their own line is marked.

## 2. The vote (`POST /shareouts/{id}/submit/`)

A members' vote opens for 7 days. Its question names the total, the number of members and
the first 8 characters of the figures' hash. Its details list every member's payment.
Members may change their vote until it closes. The figures cannot change once they go to
the vote. A draft can be cancelled (`/cancel/`) before that.

## 3. Paying (when the vote passes)

- A `shareout_approved` ledger entry records the cycle, the figures' hash, the totals and
  the vote.
- Each member's unpaid fines and open loan are paid **from their share, inside the books**.
  These are `fine_paid` and `loan_repayment` entries with source `shareout`. They move no
  bank money, so the daily bank check leaves them out, as it does for exits.
- **Each payment is proposed in money out**, approved by the officials as usual. No second
  members' vote opens for it, because the members voted on the whole share-out.
  - An official's own payment is proposed by that official, because neither the proposer
    nor the person paid may approve.
  - Otherwise, with three officials, one of them could never reach two approvals.
- If money out is off, or the chama is in breach, the payments wait. An official retries
  with `POST /shareouts/{id}/propose/`.
- A member with nothing for the bank to pay (everything went to fines or a loan) is
  settled in the books with a `shareout` entry.

## 4. Done

Each payment's `payout` entry carries `savings_out_cents`. The savings given back come off
the member's savings in `projections.position`, so they can never be paid twice: not at
exit, not in the next share-out, and not in a loan limit. The share-out is `done` once
every payment is matched in the bank.

A failed or lapsed vote leaves it `rejected`. Nothing changes.

## Not yet

- Deducting months behind (arrears) from the share. Today only fines and loans come off.
- One bulk bank payment for everyone. Each member is still one payment out.
