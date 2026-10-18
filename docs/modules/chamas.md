# chamas

Phase 0 holds only what the ledger needs to point at.

- `Chama`: name, type (merry-go-round, table banking, welfare, investment club), state,
  mode (`records_only` until its officials are verified, then `money_out`), and the
  member-code prefix (`JC-4821`).
- `Membership`: a person in a chama, with their member code (`JC-4821-017`), role and
  state. One open membership per person per chama.

Both are history: they are referenced with `PROTECT` and never deleted.

Phase 1 adds creating a chama, invites, rules, bank accounts and the states in Part 5.1
and 5.2 of the proposal.
