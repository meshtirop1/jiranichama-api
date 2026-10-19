# Investments (4.10, Phase 5)

What the chama owns besides money in the bank: land, shares, a money market fund, a bond,
a business. Every member sees the portfolio.

## Recording one

An official records it (`POST /chamas/{id}/investments/`): what kind, a name, details, the
day it was bought, and either
- **the money-out payment that bought it** (`purchase_id`): it must be paid, and the cost
  is what left the bank. One payment, one investment; or
- **a cost**, for something the chama owned before it used the app.

An `investment_recorded` entry goes on the ledger (with the payment's receipt when there
is one).

## Valuations

An official records a value on a date with its source (a valuer's report, an NSE price, a
fund statement) and, optionally, a document; its SHA-256 is kept
(`POST /investments/{id}/valuations/`). It **counts only once a different official
confirms it** (`POST /valuations/{id}/confirm/`), which writes a `valuation` entry naming
both. An investment without a confirmed valuation is shown at cost.

## Income and sale

Dividends, interest, rent and sale proceeds reach the bank like any money and appear as
unmatched credits. The treasurer assigns one to the investment
(`POST /investments/{id}/income/` with the bank line and the kind). That writes an
`investment_income` entry, which is bank money, so the daily reconciliation counts it. A
`sale` marks the investment sold on the bank date.

## The portfolio

`GET /chamas/{id}/investments/` gives every holding with its cost, latest confirmed value,
income so far and valuation history, and the totals: holdings, cost, value, the bank
balance from the last reconciliation and the net worth (bank plus holdings).
