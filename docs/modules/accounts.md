# accounts

People and sign-in. The flow is in [Phone sign-in](../flows/phone-sign-in.md).

## User

- Identified by phone. The number is stored encrypted (`phone_enc`) and found by a keyed
  hash (`phone_hash`); only a hint like `+2547••• ••• 678` is kept readable.
- `pin_hash`: the app PIN, hashed like a password.
- `language`: `en` or `sw`, used for SMS.
- **User rows are never deleted.** Memberships, chamas and audit entries point at them with
  `PROTECT`. Deleting an account calls `anonymise()`, which removes the phone, name and PIN
  and closes the account; the chama's history keeps only the member code.

## OtpChallenge

One sign-in code: a keyed hash of the code, its expiry (5 minutes), wrong tries (max 5)
and when it was used.

## Staff

Staff are users with `is_staff`, created with `manage.py create_staff`. They sign in to
the [staff console](../flows/staff-console.md) with a password and an authenticator code;
they never use the member app's SMS sign-in for the console.
