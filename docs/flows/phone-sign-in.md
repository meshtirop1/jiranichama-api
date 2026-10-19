# Phone sign-in

Members sign in with their phone number and a 6-digit code sent by SMS. After the first
sign-in they set a 6-digit app PIN.

## Steps

1. `POST /api/v1/auth/otp/` with `phone` (any Kenyan format: `0712…`, `+254712…`).
   The number is normalised to `+254…`, and a code is sent by SMS in English or
   Kiswahili. The reply shows only a hint of the number (`+2547••• ••• 678`).
2. `POST /api/v1/auth/verify/` with `phone` and `code`. A right code returns an access
   token (15 minutes), a refresh token (30 days, rotated on use), and whether the
   account is new and has a PIN. A new phone number creates the account here.
3. `POST /api/v1/auth/pin/` with `pin` sets the PIN; changing it later needs
   `current_pin`. Very common PINs (`123456`, `000000`…) are refused.
4. `PATCH /api/v1/auth/me/` with `display_name` (and `language`): the name other members
   see. The app asks for it with the PIN. `GET /auth/me/` reads it back.
5. `POST /api/v1/auth/refresh/` swaps a refresh token for new tokens;
   `POST /api/v1/auth/logout/` ends the session by blacklisting the refresh token.

## Why it is built this way

- **Phone numbers are never stored in plain text.** The number is encrypted; lookups use a
  keyed hash (HMAC), so a stolen database does not list members' numbers.
- **Codes are never stored either**, only an HMAC of the code bound to its challenge id.
- **The SMS is sent from the request, not a queue**, so the code never passes through the
  message broker.
- **Two SMS providers** are tried in order, so one provider's outage does not lock
  members out (see [notify](../modules/notify.md)).

## Limits

| Limit | Value |
| --- | --- |
| Code lifetime | 5 minutes |
| Wrong tries per code | 5, then the code is locked |
| Codes per phone | 3 per 10 minutes |
| Code requests per IP | 20 per hour |
| Verify attempts per IP | 60 per hour |

The two per-IP limits can be changed with `OTP_THROTTLE_RATE` and
`OTP_VERIFY_THROTTLE_RATE`; only local end-to-end runs raise them, because every test user
signs in from 127.0.0.1.

A wrong try is saved before the refusal is returned. (An early draft raised inside the
database transaction, which rolled the counter back and allowed unlimited guesses; the
test `test_wrong_code_counts_attempts_then_locks` guards against that.)

## Errors

Every error is an RFC 9457 problem document with a `code`: `otp_wrong` (with
`attempts_left`), `otp_expired`, `otp_locked`, `otp_rate_limited`, `pin_wrong`,
`account_inactive`, `invalid` (with field `errors`).
