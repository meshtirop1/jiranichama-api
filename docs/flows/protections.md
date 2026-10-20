# Protections: a few people cannot turn the chama against one (2026-10-20)

The owner asked that no group of officials or members can use the app's own rules against
one member: three founders outvoting one, a 5-5 split, a treasurer going to the bank alone,
a member "declared dead" to take their savings. These are the rules the app now enforces
for every chama. A chama's own rules cannot switch them off.

## A real group

- **Five founding members at least.** A chama does not go active with fewer
  (`chamas.services.MIN_MEMBERS`). `adoption-status` returns `min_members` so the app can
  say why.
- **Small votes need everyone.** When fewer than five members can vote on something, it
  passes only if every one of them says yes (`votes.services.MIN_VOTERS`). With some
  still to vote and nobody against, it lapses; one "no" fails it. In a group of three,
  "majority" would be two people.
- **A tie changes nothing.** A tied vote fails, and the ledger decision says `tied: true`
  so everyone can see why.

## Changing the rules

At least half of all members must take part, and two-thirds of **all eligible members**
(not just of those who voted) must say yes (`RULE_CHANGE_MIN_QUORUM`,
`RULE_CHANGE_MIN_PASS`, `Vote.over_all`). A chama's rules can ask for more, never less.

## Leaving

The exit deduction is at most 10% (rule schema), and a member gets the deduction shown
on the day they gave notice: a rule changed during their notice never applies to them.

## A member reported as dead

1. An official reports it. They cannot report themselves, and there can be only one open
   report. The member stays **active**; nothing is paid. The member gets an SMS at once
   and sees "you have been reported dead — I am alive" first on their home screen.
2. A **second official**, not the one who reported it, must confirm it
   (`POST /exits/{id}/confirm-death/`, append-only `DeathConfirmation`).
3. Nothing is settled for 14 days (`DEATH_OBJECTION_DAYS`).
4. The report is cancelled, and every member is told by SMS who made it, if:
   - the member taps "I am alive" (their own cancel), or
   - the member uses the app after the report was made.
5. Only then is the member marked deceased (a ledger entry) and the settlement proposed
   to the next of kin as before.

## Money that left the bank without approval

- Every unapproved withdrawal is alerted, records-only chamas included.
- A line counts as a bank charge (recorded quietly) only if the narration reads like the
  bank's own charge **and** it is no more than about KES 1,000 in the chama's currency.
  "SCHOOL FEES" is a withdrawal like any other.
- An official's explanation does not close it. The members vote on it (the official who
  explained does not vote). If they reject it, it stays open and can be explained again.
- While any unapproved withdrawal is open the chama is **in breach**: reconciliation shows
  `breach` even when the figures agree, and no new payment can be proposed (except an
  exit settlement).
- A proposal made after the money left cannot be linked to cover it (`approved_after`).
- The official who proposes a payment cannot also approve it.

## Disputes that take money from a member

If the outcome would take money from a member (a negative correction, or reversing an
entry that credited them), officials cannot decide it alone: the members vote, without
the affected member, the person who raised it, or anyone involved, with at least half
taking part and two-thirds agreeing.

## Not yet

Bank mandate record, statements on the chain and Smile ID KYC follow in later PRs (see the
protection plan of 2026-10-20). The pinned platform key, member countersigning and the
public lot for tied elections are in [trusting the records](trust.md).
