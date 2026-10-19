# Loans with guarantors (5.8, rules 12.1-12.4)

**Switched off.** Loans run only for chamas on the `loans` flag's allowlist, and only when
the chama's rules have a `loans` section (table banking has one by default). The flag
stays off until the advocate confirms that recording a chama's own loans does not make
JiraniChama a lender or a loan app under Google Play's Financial Services policy
(decision 4). Until then, every loan endpoint answers `loans_off`.

## 1. Quote

`GET /chamas/{id}/loans/quote/?amount_cents=&term_months=` shows, from the rules:
- whether the member may borrow, with every reason they may not, each naming its rule:
  `too_new` (fewer than `min_months` of membership), `in_arrears` (unpaid contributions),
  `loan_open` (one loan at a time), `over_limit`, `term_too_long`;
- the **limit**: `savings_multiple_pct` of their savings (contributions and opening
  savings), less what they already guarantee for others;
- the terms, how many guarantors are needed, and each one's equal share;
- the **whole schedule**: every instalment, its date, principal and interest, and the
  totals. Instalments are monthly with equal principal (the last one takes the rounding).
  Interest is `monthly_rate_bp` basis points a month, either `flat` on the amount
  borrowed or `reducing` on what is still owed.

## 2. Request and guarantors

`POST /chamas/{id}/loans/` with the amount, term, purpose and the guarantors' membership
ids (exactly `guarantors` of them). Each guarantor needs free savings (savings less what
they already guarantee) to cover their share. Each gets an SMS asking them to answer in
the app.

A guarantor accepts with an SMS code that names the amount and their share
(`POST /guarantees/{id}/accept/` twice: first without a code to get one, then with it).
The SMS reads like "GUARANTEE KES 30,000 for John Otieno (your share KES 15,000)". A
guarantor can also decline (`/decline/`), and the borrower is told. A guarantee not
answered in 72 hours expires. The borrower can replace a guarantor who declined or did
not answer (`POST /loans/{id}/guarantors/`). A request not complete in 7 days expires.
While the loan is open, each guarantor's own limit drops by their share.

## 3. Approval

Once every guarantor has accepted, the limit is checked again and the loan becomes a
money-out proposal of kind `loan`, paid to the borrower. It needs the approvals the rules
set and, above the vote level, a members' vote (see [money out](money-out.md) and
[votes](votes.md)). The borrower proposes it, so the borrower can never approve it (P.1).
If the proposal is refused (for example, not enough money in the bank after other
approved payments), rejected, expires or is cancelled, the loan follows.

## 4. Disbursement

A signatory pays at the bank and marks it paid. When the debit matches, a
`loan_disbursed` entry (negative, like a payout) is written and the schedule is rebuilt
from the debit's bank date. The borrower gets an SMS with the first instalment.

## 5. Repayment and interest

The borrower pays like a contribution, with their member code. With `contribution_first`
(the default), the month's contributions, arrears and fines are paid first, and the loan
takes what would otherwise go ahead to future months or to credit. Otherwise the loan
takes its instalments due first and the contribution rules see the rest. Either way a
`loan_repayment` entry records it. A repayment never takes more than what is due on the
loan so far.

Interest is charged by a `loan_interest` entry on each instalment's due date (a daily
task at 07:30). Balances are never edited; they are worked out from the entries.
Reminders go by SMS 3 days before and on each due date.

## 6. Missed instalments and default

- An instalment still not covered `grace_days` (default 3) after its due date is
  **missed**. A `late_penalty_cents` fine (rule 12.3) is raised once for that month, and
  the loan is `in_arrears`. It goes back to `repaying` once the repayments catch up.
- After `default_after_missed` missed instalments (default 2), the loan is `defaulted`,
  the guarantees are `called`, and the borrower, the guarantors and the officials are told
  once (rule 12.4).

## 7. Repaid

When the repayments cover the principal and all the interest charged, and the schedule
has run or every instalment is paid, the loan is `repaid`. The guarantees are `released`,
and the borrower and guarantors are told.

## Not yet

- Recovery after default: deductions from the borrower's savings, then from the
  guarantors' in proportion to their shares, each as a proposal the officials sign.
- The member's ID check before borrowing (the KYC system, 5.15).
- Loan lines on member statements and in the SMS BAL reply.
- Paying a loan off early, in one payment, beyond what is due.
