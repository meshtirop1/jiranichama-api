# Leaving the chama, and the death of a member (5.13, rule 16.1)

## What comes back

`GET /chamas/{id}/exit-quote/` shows the member, from the rules' `exit` section:
- savings: contributions, opening savings and credit;
- less unpaid fines, less an open loan's outstanding balance, less `deduction_pct` (not on
  death);
- the settlement, any shortfall, the notice (`notice_days`, default 30) and the exit date.

## Leaving

1. The member confirms with an SMS code naming what they get back (`POST
   /chamas/{id}/leave/`, twice: without a code, then with it). An official must hand the
   role over in an election first.
2. The membership is `exiting`, with a `membership` entry carrying the quote. During the
   notice they still see the chama, and can change their mind (`/exits/{id}/cancel/`),
   which makes them active again.
3. On the exit date (daily task at 07:45, and on every read of the exits list):
   - unpaid fines and the open loan are **paid from the savings inside the books**:
     `fine_paid` and `loan_repayment` entries with source `exit`. They move no bank money,
     so daily reconciliation leaves them out;
   - a loan larger than the savings is put in default and **the guarantors are called**
     for the rest (5.8);
   - the settlement becomes a money-out proposal of kind `settlement`, paid to the member
     and approved by the officials like any payout. If it is refused or not approved in
     time, it is proposed again the next day. With nothing to pay, the member leaves at
     once.
4. When the settlement's debit matches, the member is `exited`, with a `membership`
   entry. Their records stay in the chama's history.

## Death

An official records it (`POST /memberships/{id}/death/`, multipart): date, the next of
kin's name and phone, and a photo of the burial permit or death certificate. The photo is
stored, and its SHA-256 goes in the `membership` entry.
- The membership is `deceased` at once; any notice to leave is cancelled.
- Fines and the loan are settled from the savings as above; a larger loan calls the
  guarantors.
- The savings go to the next of kin as a `settlement` proposal, and, if the rules set a
  `death_benefit_cents`, a second proposal pays the welfare benefit. Both are approved
  like any payout.

## Not yet

- End of cycle for merry-go-round exits (the exit date is the notice only).
- Writing off a loan up to an insured amount on death (it is deducted from savings).
- Removing personal details when a member deletes their account (6.14).

## Protections (2026-10-20)

Some of this changed so that a few people cannot use the rules against one member. See
[protections](protections.md).

## ID checks and privacy (2026-10-20)

See [ID checks and privacy](identity.md).
