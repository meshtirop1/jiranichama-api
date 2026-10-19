# M-Pesa paybill notifications (Daraja C2B, 5.3 and 6.9)

For a chama with **its own M-Pesa paybill or till**, Safaricom tells us about each
payment within seconds. The member's contribution is recorded the same minute, instead
of waiting for the next bank statement.

## Setting it up (staff console)

1. The chama authorises JiraniChama to receive its paybill notifications.
2. Staff add a **Daraja channel** in the console: chama, shortcode, paybill or till. A
   secret token is generated for the callback URLs. Like every console change, this
   needs a reason and a ticket reference and is audited.
3. Staff run **Register with Safaricom** on it. This uses the platform's Daraja app
   (`DARAJA_CONSUMER_KEY`/`SECRET`, `DARAJA_BASE_URL`) to register
   `{DARAJA_CALLBACK_BASE_URL}/api/v1/hooks/daraja/{token}/validation/` and
   `…/confirmation/` (C2B v2, response type Completed).

Web stays admin-only: members and officials never set this up in a browser.

## When a member pays

- **Validation**: we always answer Accepted. Refusing would bounce a member's money.
- **Confirmation**:
  - stored as a bank transaction from source `daraja`, once per TransID (a resend is
    ignored);
  - amount in cents, from Decimal;
  - the day from TransTime;
  - the narration "M-PESA {TransID} {BillRefNumber} {payer name}";
  - the payer's number hashed with our key on arrival. Masked or hashed numbers are not
    used.
- **Matching**, straight away, as for any credit:
  1. the member code in BillRefNumber;
  2. the payer's phone, if it belongs to exactly one member;
  3. otherwise the treasurer's unmatched queue.
- The token in the path picks the chama; a wrong token gets 404.
  `DARAJA_ALLOWED_IPS` can narrow callers to Safaricom's addresses.

## When the bank statement arrives (no double counting)

The same payment then appears as a statement credit. Before it is matched, each
statement credit is checked against this chama's M-Pesa payments that no statement line
has repeated yet:
1. by the **M-Pesa reference** in the line's reference or narration; else
2. by **amount, day (one either way) and the member code**, when exactly one fits.

A repeat is stored as `duplicate`, pointing at the M-Pesa transaction, and is never
matched or counted again (5.3 step 4).

## Reconciliation

An M-Pesa payment is in the records before any statement shows it in the bank. Until a
statement line repeats it, the daily check leaves it out of the records figure (and out
of "unmatched"), so a payment made today never shows as a gap.

## Not yet

- **Receipts** to the member by push or SMS (5.3 step 8): they see the entry in the app.
- Paybills that settle into the bank as one daily lump: those statement lines will not
  pair with individual payments and stay in the treasurer's queue.
- Bank transaction feeds (Phase 4).
