# Elections and role changes (5.12, rule 15.1)

## Electing an official

An official nominates an active member for a role in the rules (`POST
/chamas/{id}/elections/` with `role` and `candidate_id`). A member who already holds an
official role cannot stand for another. Only one election per role runs at a time.

A members' vote opens at once: "Elect Mary as treasurer for 24 months?", open for
`elections.vote_days` (default 7). It is the usual vote, by phone key, SMS code or
YES/NO reply, with the rules' quorum and pass mark.

- **Lost**: nothing changes.
- **Won**: the role starts only once the winner's **phone key is active** (and, once it
  exists, their ID check, 5.15). Until then the **outgoing official keeps the role**, so
  the chama is never short of signers. The role starts automatically when the key
  becomes active, and hourly and on every read otherwise.
- When it starts, the winner takes the role, the outgoing official becomes a member, and
  a `role_changed` entry records both, the term and the vote.

## Acting officials

If an official leaves suddenly or dies, the role is empty. The other officials can name
an **acting** official (`acting: true`; refused while someone holds the role). They take
the role at once, with a `role_changed` entry marked acting, and a members' vote to
confirm them stays open for **30 days**. If it does not pass, they step down to member,
with another `role_changed` entry.

## The bank mandate

When the chair or treasurer changes (the bank signatories), every official gets an SMS
to update the signatories at the bank. An official confirms in the app when it is done
(`POST /elections/{id}/mandate-updated/`), which writes a `role_changed` entry with
`bank_mandate_updated`.

## Too few officials

`GET /chamas/{id}/elections/` also lists every approval level that the officials still in
place can no longer reach (`election_needed`). Money out at such a level is refused with
`election_needed` ("This needs 2 officials to approve, and only 1 can. The chama needs an
election first.") instead of waiting for approvals that cannot come.

## Trust (2026-10-20)

See [trusting the records](trust.md): the platform key chain, member countersignatures, the public proof file and the public lot for tied elections.
