# Bank feeds through the bank's API (Phase 4)

Statements (Phase 1) and M-Pesa notifications (Phase 2) remain. Where a bank lets us, we
also read the chama's account directly, starting with **Equity's Jenga API**: the balance
and the full statement. **Read-only**: we never ask for payment permissions.

## Setting one up (staff console)

Once the chama has authorised read-only access, staff add a **Bank feed** in the staff
console: the chama, its bank account, the account number, the date to read from, and the
Jenga credentials (API key, merchant code, consumer secret and the account holder's RSA
private key). The credentials are stored **encrypted** and never shown again. "Pull now"
reads it at once; otherwise every active feed is read hourly (five past the hour).

## A pull

1. Jenga's merchant token, then the full statement from a few days before the last pull
   (banks post late) to today, and the current balance. Every request is signed with the
   account holder's key, as Jenga requires.
2. Each line becomes a bank transaction with source `bank_api`, and goes through the same
   matching as statement lines: credits by member code or phone, debits by money out
   (bank charges, approved proposals, or an unapproved-debit alert).
3. **Never counted twice.** A line already recorded from somewhere else is stored as a
   duplicate of it:
   - a feed line already in an accepted statement, or a statement line already read from
     the feed: by the bank's reference, otherwise by amount, direction and day when only
     one fits;
   - a feed credit already received from M-Pesa (Daraja).
   A line read twice by the feed itself is skipped (same reference).
4. The balance is kept on the feed with its time, and the daily reconciliation runs.

A failed pull (wrong credentials, the bank down) is logged with its error on the feed and
changes nothing.

## Reconciliation

The daily check uses the **feed's balance** when it is at least as recent as the last
accepted statement, and the statement's closing balance otherwise. The check says which
(`bank_source`: `statement` or `bank_api`). A chama with a feed but no statement starts its
records from the balance before the first line read (worked out on the first pull).

## Before going live

The request and response shapes follow the Jenga v3 documentation but have **not yet been
checked against a live account**. Run one UAT pull with real credentials
(`JENGA_BASE_URL`, default the UAT host) before switching a chama's feed on. Other banks
are added as they grant access.
