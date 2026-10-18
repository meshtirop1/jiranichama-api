# chamas

- `Chama`: name, type, state, mode (`records_only` until officials are verified, then
  `money_out`), member-code prefix, the draft rules during setup, and setup dates.
- `Membership`: a person in a chama, with member code (`JC-4821-017`), role, state,
  whether they are a founding member, when they joined and confirmed the rules.
- `Invite`: a 6-letter code, optionally tied to one phone (always, for officials).
- `BankAccount`: bank, encrypted account number (last four shown), paybill or till, and
  how statements arrive. One active account per chama.
- `StatementInbox`: the chama's own address for bank e-statements.
- `services.py`: create, bank account, draft rules, invites, joining, adoption,
  activation, expiry. See [Setting up a chama](../flows/chama-setup.md).
- `access.py`: member, official and founder checks. Someone outside a chama gets
  "not found", so membership is not revealed.

All of these are history: referenced with `PROTECT` and never deleted.
