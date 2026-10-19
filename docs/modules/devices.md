# devices

Phase 1: **SMS signing codes** (`SigningChallenge`), the design copied from JiraniSoko
Motors. Used to join a chama, adopt its rules and confirm a statement.

- A code is bound to one person, one purpose, one subject and a hash of exactly what they
  were shown. If that changes, the code stops working.
- The SMS names the action: "123456 is your code to CONFIRM the bank statement 1 Mar to
  31 Mar, closing balance KES 1,284,500.00. Never share this code."
- 5 minutes, 5 tries (a wrong try is saved before it is refused), one use, at most 3 codes
  for the same action within 5 minutes. Only a keyed hash of the code is stored.

Phase 2 adds device keys in the phone's secure chip for approving money going out.
