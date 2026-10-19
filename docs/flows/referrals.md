# Referrals to licensed partners (4.10, decision 18)

JiraniChama lends nothing and insures nothing. It may introduce a chama to a partner who
does, but only to a **licensed** partner and only with the **chama's recorded consent**.

## Partners (staff console)

Staff add each partner after checking its licence: a bank (CBK) or SACCO (SASRA) for
group accounts or group loans, or an insurance broker (IRA) for welfare cover, with the
licence number. Members see the active partners at `GET /partners/`.

## Consent

1. An official proposes a referral to one partner, with a reason and the official the
   partner may contact (`POST /chamas/{id}/referrals/`). Only one referral per partner can
   be open at a time.
2. A members' vote opens at once (7 days): "Agree to be introduced to Umoja SACCO (group
   loan): …?".
3. If it passes, a `referral_consent` entry goes on the ledger: the partner, its
   regulator and licence number, the product, the contact, the vote, and exactly what
   will be shared. If it fails or lapses, nothing is shared.
4. Until it is sent, an official can withdraw the consent
   (`POST /referrals/{id}/withdraw/`), which also goes on the ledger.

## Passing it on (staff console)

For a consented referral only, staff see the hand-over: the chama's name, its type, how
many members it has, and the contact official's name and phone. Nothing else about the
chama or its members is passed on. Staff then mark it sent.

## Not yet

Partner agreements and referral fees are the owner's to arrange; there are no partners
until then.
