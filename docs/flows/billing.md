# JiraniChama's monthly bill (2026-10-21)

The owner decided the pricing on 2026-10-20 (see the countries plan):

- **Price:** about USD 0.10 per **active** member per month, in local money and rounded.
  That is KES 15, UGX 400 or TZS 300. Other currencies use KES 15 scaled by the rough KES
  rate, to two significant figures.
- **Active** means the member has at least one record in the chama that month. Members
  who left, or did nothing, cost nothing.
- **Free:** the first **three** months after the chama goes active, and any month with
  **five active members or fewer**.
- **The chama pays one bill.** It may pass the cost on to members as a levy, under its
  own rules.
- ID checks the chama paid for (Smile ID, rule 6.1) are added at `KYC_PRICE_USD_CENTS`,
  in local money.

## How it works

- On the 1st at 03:00, `billing.tasks.issue_monthly_bills` writes last month's `Bill` for
  every active chama. There is one bill per chama per month; the bill is insert only, and
  only its payment moves. A bill of zero is `free`; anything else is `open`, due on the
  15th of the next month.
- `GET /chamas/{id}/bills/` shows every member this month so far, the last 24 bills, the
  pricing, and where to pay. What the chama pays us is the members' business too.
- **Paying.** The chama pays JiraniChama's M-Pesa paybill (`BILLING_PAYBILL`), with the
  account `JC` plus its member-code prefix. The treasurer proposes the payment like any
  money out, so it shows in every member's records like every other payment. Until the
  paybill is set, the app says nothing is due yet.
- **Recording a payment.** Staff mark a bill paid on the staff console ("Mark paid"). The
  action is audited.
- The bill is not on the chama's ledger: it is our fee, not the chama's money. The
  chama's own payment of it is.

## Not yet (owner)

- JiraniChama's own paybill, and automatic matching of bill payments.
- What happens to a chama that does not pay (grace period, then read-only).
- Tax on the fee.
