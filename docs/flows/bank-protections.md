# Bank protections: signatories, sealed statements, no gaps (2026-10-20)

The owner asked that a treasurer can never "go alone to the bank", and that the bank's
transaction history be tied to JiraniChama. No bank or mobile-money provider in Kenya,
Uganda or Tanzania lets an app act as an approver, so the protections work with the bank
instead of around it.

## The bank mandate (`chamas.BankMandate`, `apps/chamas/mandate.py`)

The mandate is the bank's own record of who may sign and how many must sign. Here is how
it gets into JiraniChama:

1. An official opens **Running the chama → Bank signatories**. They tick the signatories
   on the bank's form, say how many must sign each withdrawal, and attach a photo or PDF
   of the form (`POST /chamas/{id}/bank-mandate/`).
   - At least two signatories, and at least two must sign (`one_person_can_sign`
     otherwise).
   - "Each signatory approves in the bank's app" is ticked when the account is a
     multi-approval product. The country's known products are listed: in Kenya, Equitel
     Multiple Approval, Postbank M-Chama and BOA e-Chama; in Tanzania, Vodacom M-Koba.
2. A **different official** checks it against the paper and confirms with an SMS code
   (`POST /bank-mandates/{id}/confirm/`, twice). The uploader cannot confirm their own.
3. On confirmation, a `bank_mandate` ledger entry seals it. The entry holds:
   - the signatories' member codes;
   - how many must sign;
   - the bank-app flag;
   - the file's SHA-256;
   - who uploaded it and who confirmed it.

   The previous mandate becomes `replaced`. Every member sees it, and can download the
   form (`/bank-mandates/{id}/file/`).
4. Until there is a confirmed mandate needing two signatures, every member's overview
   says so, and **money out cannot be switched on** (`mandate_missing`). The chama JSON
   carries `mandate_safe`.

## Statements on the chain

Every accepted statement, from the bank's email or an official's upload, is now itself a
ledger entry: `statement_accepted`. It records:

- the route;
- the file's SHA-256;
- the period;
- the opening and closing balances;
- the number of lines, and a SHA-256 over every line (date, reference, credit, debit,
  balance);
- the flags;
- who uploaded it and who confirmed it.

The bank history the chama relied on cannot later be swapped without breaking the chain.
The Records tab shows it as "Bank statement sealed".

## No skipped periods

Before, a gap between statements was only flagged. Now:

- **An official's upload** with a gap is refused. The official must upload the missing
  days first, because a gap is where a withdrawal could hide.
- **The bank's own email** is still accepted, flagged to every member.

## Uploads must agree with the bank's feed

Where the bank's API (Jenga, KCB Buni, Co-op) already reported transactions for the same
days, an uploaded statement must match them line for line: same dates, directions and
amounts. Otherwise it is refused as `differs_from_bank_feed`. A doctored PDF cannot hide
a withdrawal the bank already reported.

## The M-Pesa callback

Each confirmation must:

- be for this channel's own `BusinessShortCode`;
- carry an M-Pesa-shaped receipt (8–12 capital letters and digits);
- have an amount above zero and at most KES 1,000,000.

Anything else is ignored, while we still answer "Accepted". Behind our own proxy, set
`DARAJA_TRUST_PROXY=true`: the allow-list (`DARAJA_ALLOWED_IPS`) then checks the address
the proxy saw (the last X-Forwarded-For entry), never one the caller wrote. A forged
credit would in any case show as a reconciliation gap against the next statement.
