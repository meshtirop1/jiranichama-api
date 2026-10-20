# Money out (5.6, 5.7)

JiraniChama decides whether a payment out is allowed and records who approved it. The
bank signatories still move the money at the bank, as they do today. Every debit on a
bank statement is then matched back to an approval, or every member is told.

## Switching it on

A chama starts in **records only**. Money out is switched on when **two officials** each
agree by SMS code (`POST /chamas/{id}/money-out/`, two steps). Until then, debits on
statements just wait (`debit_pending`). Bank charges are recorded either way.

Interim: the design gates money out on every official passing the ID check and
registering a phone signing key (6.7). Those arrive in later slices; the two-official
switch stands in until then.

## A proposal, step by step

1. **Propose** (any official, `POST /chamas/{id}/proposals/`). A *payout* goes to a
   member; a *withdrawal* goes to anyone else (supplier, asset), with the payee typed in.
   Amount, purpose and optionally the payee's account.
2. **Rules check**, in this order. Each refusal names its rule, and the proposal is
   kept, refused, for everyone to see:
   - the payee is not behind on contributions (rule 3.1);
   - a bank statement has been accepted, and the amount fits the last reconciled balance
     less money already promised (approved or paid but not yet matched) (rule 0.1);
   - the approval level for the amount (rules 4.x) sets how many officials must approve.
     A level that also needs a members' vote opens one alongside
     ([Members' votes](votes.md)); the proposal is approved only when the vote has passed
     too, and rejected if it fails or lapses.
3. **Approvals.** Each official sees a plain summary ("APPROVE paying Mary KES 20,000 for
   merry-go-round turn") and approves with an SMS code naming exactly that
   (`POST /proposals/{id}/approve/`, two steps). The proposer may approve; one person
   counts once. **The payee never approves**, even as an official. Officials whose phone
   holds a signing key approve with their fingerprint instead, and SMS is refused for
   them ([Phone signing keys](device-keys.md)). Enough approvals: the
   proposal is `approved`.
4. **Decline** with a reason (`/decline/`). When the officials left cannot reach the
   number needed, the proposal is `rejected`.
5. **Deadline**: the rules' hours (default 72). After that the proposal is `expired`
   (hourly task, and on every read).
6. **Cancel**: the proposer, before payment (`/cancel/`).
7. **Paid**: a signatory pays at the bank and taps Paid with the bank reference
   (`/paid/`), so the proposal is `paid_pending_match`.
8. **Matched**: when a statement brings a debit of exactly that amount (and containing
   the reference, if one was given), a `payout` ledger entry is written: negative amount,
   payee, purpose, who approved and when. The proposal is `paid`. An approved proposal
   also matches a debit before anyone taps Paid.

## Debits on a statement

Every debit from an accepted statement goes through `process_debit`:
- **Bank charge** (narration has fee, charge, commission, excise, levy and the like):
  a `bank_charge` entry, no alert.
- **Matches an approved proposal**: as step 8.
- **Anything else** (money-out chamas): an `unapproved_debit_detected` entry, the bank
  line is `unapproved`, and **every member gets an SMS the same day** ("ALERT: KES 45,000
  left Umoja's bank account on 14 Mar with no approval in JiraniChama…"). Officials then:
  - **link** it to an approved proposal that was paid without tapping Paid
    (`/unapproved-debits/{id}/link/`): a `payout` entry records it, naming the alert it
    resolves; or
  - **explain** it (`/explain/`): every member sees the explanation.

The alert entry stays on the chain either way: nothing is ever deleted.

## Not yet (later Phase 2 slices)


the merry-go-round rotation (turns, draw, arrears handling); the 72-hour escalation of
an unexplained debit to a dispute; reminders when a Paid debit does not appear; retro
approvals; amount mismatches handled as partial matches (today a different amount is
simply unapproved).

## Loans

A loan is a proposal of kind `loan`, opened by the borrower once every guarantor has
accepted ([loans](loans.md)). It is approved like a payout to a member. When its debit
matches, the entry is `loan_disbursed` instead of `payout`, and the loan's schedule starts.

## Protections (2026-10-20)

Some of this changed so that a few people cannot use the rules against one member. See
[protections](protections.md).
