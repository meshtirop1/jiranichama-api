# Members' votes (5.11, rule 9.1)

Phase 2 uses votes for **payouts above the rules' vote level** (approval levels 4.x with
"members also vote") and **rule changes** ([rule changes](rule-changes.md)), which bring their own thresholds. Elections and loans reuse the same votes later.

## Opening

When an official proposes a payment whose level needs a vote, the proposal is not
refused (as it was before votes existed). It collects the officials' approvals, and a
**vote opens alongside it**:
- **Question**: for example "Pay Mary KES 60,000 for Welfare: hospital bill?".
- **Who votes**: every active member except the person being paid.
- **Settings** are copied from the rules (`votes`: quorum %, pass %, secret) when the vote
  opens, so a later rule change cannot alter a running vote. Without a `votes` section,
  half must vote and more than half must say yes, openly.
- **Closes** at the proposal's deadline.

The proposal is approved only when **both** enough officials have approved **and** the
vote has passed.

## Voting

Each eligible member votes **yes** or **no**, once:
- **With the phone key** (fingerprint, face or PIN): `POST /votes/{id}/signing-payload/`
  with the choice, sign, then `POST /votes/{id}/signed-ballots/`. The payload names the
  vote, question, choice and a single-use nonce. A signature over the other choice does
  not count.
- **By SMS code**, for members without the app's key: `POST /votes/{id}/ballots/` with the
  choice sends a code naming it ("VOTE YES: Pay Mary…"), and the same call with the code
  casts it. A member with an active phone key must use it.

The first ballot counts; a second is refused.

While the vote is open everyone sees how many have voted and who, but **not the tally**,
so nobody votes with the crowd.

## Secret votes

When the rules say so, the ballot is stored without the member, without the phone key
and without its signature. A keyed hash stops a second ballot, and the list of who voted
is kept separately (`Turnout`). Ballots carry no time, so their order cannot be matched
to the turnout list. The voter is still checked (code or signature) before the ballot is
written; only what would name them is left out.

## Closing and counting

A vote closes when every eligible member has voted, or at its closing time (hourly task,
and on every read):
- **Lapsed**: fewer voted than the quorum.
- **Passed**: more than the pass percentage of the votes cast said yes. An even split is
  not a pass.
- **Failed**: otherwise.

A `vote_result` ledger entry records the question, result, yes and no counts, eligible
count, settings, and the member codes of who voted. If the vote failed or lapsed, the
proposal is rejected, saying why.

## Not yet

Votes for rule changes, new members, elections and loans; reopening a lapsed vote once;
meetings and attendance. (SMS replies from basic phones: see [SMS commands](sms-commands.md).)

## Protections (2026-10-20)

Some of this changed so that a few people cannot use the rules against one member. See
[protections](protections.md).
