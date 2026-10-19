# USSD menu (4.9)

For members on any phone: dial the chama's USSD code and read the menu. **Read-only**:
nothing that changes records can be done by USSD. Votes and joining stay on SMS codes and
the app.

## How it works

Africa's Talking calls `POST /hooks/ussd/{USSD_TOKEN}/` on every step of a session with
`sessionId`, `serviceCode`, `phoneNumber` and `text`, the whole path typed so far
(`1*6*ABCD2345`). The router is stateless: the path is the state. The reply is plain text
starting with `CON` (the menu continues) or `END` (the session closes), at most 182
characters (longer answers are cut with "…").

- The caller is known by their number (hashed). A number we do not know gets "not
  registered" and nothing else.
- With more than one chama (three at most), the first screen picks the chama.
- The menu, in the member's language (English or Kiswahili):
  1 Balance, 2 Next due, 3 Loan, 4 Fines, 5 Rules, 6 Verify a receipt (asks for the
  code). The answers are the same as the SMS commands for that one chama.
- Each step is logged (`UssdRequest`: number hash, session, depth; never what was
  typed). A number may make `USSD_REQUESTS_PER_HOUR` steps an hour (default 60).

## Settings

`USSD_TOKEN` (the secret in the hook path; empty switches USSD off) and
`USSD_ALLOWED_IPS` (Africa's Talking's addresses, optional). The hook is registered
against the USSD code in the Africa's Talking dashboard. A shared code comes first, a
dedicated one only after traction (decision 12).
