# ID checks and privacy (2026-10-20)

Owner decisions: the ID check uses Smile ID, the chama pays, and identity must be
maintained while privacy is preserved.

## The ID check (`apps/identity`)

- **Off until configured.** `GET /me/identity/` says `available: false` until the server
  has `SMILE_ID_PARTNER_ID` and `SMILE_ID_API_KEY`. Until then the app shows "Check my
  ID: coming soon". `SMILE_ID_PRODUCTION=true` switches from Smile ID's sandbox to
  production.
- **How it works: Smile ID Enhanced KYC** (job type 5, `/v1/id_verification`, signed
  with HMAC-SHA256 of timestamp, partner id and `sid_request`).
  1. The member gives the kind of ID, its number and their name as on the ID, and chooses
     which of their chamas pays.
  2. The number is looked up once in the country's registry.

  ID types by country:

  | Country | ID types |
  |---|---|
  | Kenya | National ID, Alien card, Passport |
  | Uganda | National ID |
  | Tanzania | NIDA (NIN) |
- **Passing** needs result code `1012` (a valid ID) **and** the registry's name must
  match the person's JiraniChama name: at least two words in common, in any order. A
  valid ID under someone else's name fails.
- **What we keep** (`IdentityCheck`, insert only apart from the result):
  - the status and result code;
  - the registry's name;
  - the last four digits;
  - a **keyed hash** of country, ID type and number;
  - who paid and the price (`KYC_PRICE_USD_CENTS`), for the chama's bill.

  We keep no ID number and no photo.
- **One person, one ID.** A verified ID's hash cannot be verified again on another
  account (`id_already_used`). Nobody can be two members, or vote twice from two phones.
- **What others see.** `/chamas/{id}/members/` carries `id_checked: true`, shown as a tick
  next to the name. `/auth/me/` returns the person's own `kyc_status`.
- **Rule P.3.** "Money can go out only after every official has passed the ID check."
  With `KYC_ENFORCED=true`, money out cannot be switched on until every official is
  verified (`officials_not_verified`). It is off by default, so chamas are not blocked
  before Smile ID is set up.
- **Not yet.** The selfie check (SmartSelfie) needs Smile ID's mobile SDK in the app.
  Matching the payer's name on M-Pesa against the verified name is also not built yet.

## Privacy fixes

- **Next of kin encrypted.** A reported death's next-of-kin name and phone are encrypted
  at rest (`Exit.next_of_kin_enc`, Fernet like phone and bank numbers). Existing rows
  were encrypted by the migration, and the plain columns are gone.
- **No full numbers in vote SMS.** A payout vote's question goes to every member by SMS.
  It now names the payee with only the last four digits of their phone or account.
- **Staff need a support grant.** On the staff console, a chama's records (ledger
  entries) are listed only while its officials have an active support grant that covers
  the ledger ([support access](support-access.md)). Other chamas' money is not staff's
  business.
