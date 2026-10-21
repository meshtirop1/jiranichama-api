# Disputes (5.14, rule 14.1)

Any member can say a record is wrong. The records never change because of it; the outcome
is added as new entries.

## Raising

`POST /chamas/{id}/disputes/` with what it is about (`subject_type` and `subject_id`), a
reason (`wrong_amount`, `not_mine`, `wrong_rule`, `not_approved`, `other`) and the member's
words:
- `entry`: a ledger entry. The member it belongs to is involved.
- `fine`: the fined member is involved.
- `proposal`: money out, including loans. The proposer, the payee and every official who
  answered are involved.
- `payment`: a bank line. Whoever it was matched to and the treasurer are involved.
  **"Not me"** opens one of these automatically (5.3).
- `minutes`: the secretary who wrote them is involved.
- `other`: a rule outcome or a breach, in words. The officials are involved.

The member raising it is never one of the "involved". Each involved person gets an SMS.
Every member sees the dispute, unless it was raised as **officials only** (a personal
matter, if the rules allow it). Then only the officials, the member who raised it and the
people involved see it.

**Money out that is still waiting** (approvals or payment) **pauses** while a dispute
about it is open: no approvals and no "paid" until it is settled (`disputed`). Nothing
else freezes.

## Responding

The people involved have `response_hours` (default 72) to respond
(`/disputes/{id}/respond/`). Others can add notes.

## Settling

When everyone involved has responded, or the time is up, an official who is **not
involved** (and did not raise it) proposes an outcome (`/outcome/`):
- `no_change`, with the reasons;
- `reversal` of the disputed entry (only for an entry not yet reversed);
- `correction` of an amount for a member: a `correction` entry, counted in that member's
  money (held as credit for their next months);
- `refund` of an amount to a member: recorded, then paid through money out like any payout.

The rules' `disputes.decided_by` decides who settles it:
- `officials` (default): the proposer counts as agreeing, and other uninvolved officials
  agree with an SMS code naming the outcome (`/agree/`) or disagree (`/disagree/`). Once
  `officials` (default 2) agree, it is settled. Once enough disagree that this can no
  longer happen, the outcome is dropped and another can be proposed.
- `vote`: a members' vote on the outcome; passed settles it, anything else drops it.

If too few officials are free of the dispute to settle it, the proposal is refused
(`not_enough_deciders`); the chama needs a vote (change the rules) or to take it outside.

A settled dispute writes the outcome's entries and a `dispute_resolved` entry (outcome,
text, who agreed or that the members voted), and its receipt shows on the dispute.

## Withdrawn or taken outside

The member who raised it can withdraw it. They, or an official, can mark it **taken
outside** the chama (mediation, a Small Claims Court, the police), saying where.
JiraniChama staff never decide disputes. They, and the chama, use the **evidence pack**:
`GET /disputes/{id}/evidence/` gives the disputed entries with their rule references,
hashes and receipts, the fine, proposal approvals, bank line or minutes, and every message.

## States

`awaiting_response` → `deciding` → `resolved`, back to `awaiting_response` when an outcome
is dropped, plus `withdrawn` and `escalated_outside`.

## Protections (2026-10-20)

Some of this changed so that a few people cannot use the rules against one member. See
[protections](protections.md).

## Disputes JiraniChama opens by itself (2026-10-21)

Money that left the bank with no approval and no explanation for 72 hours becomes a dispute
with no member as the raiser (`automatic: true`, shown as "JiraniChama"). The bank
signatories on the confirmed mandate are involved, or the officials when there is none.
See [automation](automation.md).
