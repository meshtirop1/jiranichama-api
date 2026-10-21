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

## ID checks and privacy (2026-10-20)

See [ID checks and privacy](identity.md).

## Richer voting (2026-10-21)

The owner said the voting system was "too basic". It now has:

- **Abstain.** A member may abstain: it counts as taking part (quorum), never for or
  against. In a small group (fewer than five), everyone must still say yes, so an
  abstention there means the vote does not pass.
- **Changing a vote.** Where the vote allows it (`allow_change`), a member may change their
  vote until it closes. Each change is a new ballot with the next `round`, and the highest
  round counts. Who voted stays one row, and secret ballots still carry no time.
  - Votes other flows open follow the rules' `votes.allow_change` (default off: the first
    vote counts).
  - Members' motions and share-outs always allow changing.
- **Polls.** A vote of kind `choice` has two to six options. A ballot is the option's number
  ("1" to "6") or `abstain`. The one option with the most votes wins once the quorum is
  reached; a tie at the top decides nothing (`tied: true`). The result entry records the
  options, the count for each and the winner.
- **Members' motions.** Any active member raises a motion (`POST /chamas/{id}/motions/`): a
  question, why, yes/no or a poll, and how many days the vote runs (1 to 14). It goes to a
  vote once `votes.motion_seconders` other members (default 2) second it
  (`POST /motions/{id}/second/`).
  - The raiser cannot second their own motion.
  - Officials cannot block it.
  - One member may have two motions waiting for seconds at a time.
  - A motion not seconded within a week expires. The raiser may withdraw it while it waits.
  - A motion's result is the chama's recorded decision. Money still moves only through money
    out, and the rules change only by a rule change.
- **Comments.** Members write their view on an open vote (`/votes/{id}/comments/`), for
  everyone to read before voting. Comments are insert only.
- **Reminders.** Every eligible member sees a new vote in the app's notices, and members
  who have not voted hear again a day before it closes (see [automation](automation.md)).
- **From basic phones.** `ABSTAIN 4821` (`NAJIZUIA`), and for a poll the option's number
  then the vote's number: `2 4821`. The invitation SMS lists the options with the replies.

The rule book says all this in clause 9.2.
