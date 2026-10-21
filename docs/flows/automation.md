# The chama runs itself (2026-10-21)

The owner asked to make JiraniChama "really super automatic". Officials should only step
in to approve. Everything else should chase itself. This page covers the shared notices,
automatic chasing, payment reminders, Pay now and the monthly pack. Smart matching is in
[payments](payments.md#smart-matching-2026-10-21), the share-out in [share-out](shareout.md),
and the richer voting in [votes](votes.md#richer-voting-2026-10-21).

## Notices: tell one person, once (`apps/automation`)

`notices.notify(user, key=…, text={en, sw}, sms=…)` writes one `Notice`.

- **Once per key.** The key is unique per person (`due-soon:2026-11:<member>`), so a job
  can run many times and send each reminder once. The table is insert only, except for
  when the notice was read and whether the SMS went.
- **The app first.** Every notice shows in the app (`GET /me/notices/`, a bell with the
  unread count). `POST /me/notices/read/` marks notices read.
- **SMS only when it helps:**
  - `away` (the default): only to people who have not opened the app in 30 days, or never.
    Those are the basic-phone members.
  - `always`: for things someone must act on soon, such as an approval about to run out.
  - `never`: the app only.

  This keeps the SMS bill small. Push notifications will ride on the same notices once
  Firebase is set up.

## Automatic chasing (hourly at :25, `chasers.py`)

| What waits | Who hears | When |
|---|---|---|
| An approval about to run out | Officials who may approve and have not answered (SMS) | 24 h before the deadline |
| A vote about to close | Members who have not voted; basic phones get their reply number again | 24 h before it closes |
| Money marked **Paid** but not in the bank | The officials, then every member | After 3 days, then after 7 |
| Cash a member confirmed but not banked | The treasurer (SMS) and every member | After 7 days |
| Cash recorded but not confirmed by the member | That member (SMS) | After 2 days |
| A statement waiting for a second official | The other officials (SMS) | After 1 day |
| Minutes not written | The secretary, or the officials if there is none (SMS) | 3 days after the meeting |
| Money that left with no approval and no explanation | A dispute opens by itself | After 72 hours |

**The automatic dispute.** `disputes.raise_automatic` opens it with no member as the one
who raised it (`raised_by` empty, `automatic: true`, shown as "JiraniChama"). The bank
signatories on the confirmed mandate are the people involved, because they are the ones
who can withdraw. Without a mandate, the officials are involved. From there it runs like
any [dispute](disputes.md).

**Every morning at 08:00** the treasurer hears how many payments wait to be matched, and
that suggestions are ready.

## Payment reminders (08:00, `reminders.py`, rule 3.7)

Every member who still owes is reminded once per month and stage:

- **soon**: `days_before` days before the due date (default 3);
- **today**: on the due date;
- **late**: the day after the grace period ends, if still unpaid. The grace period may run
  into the next month.

Each reminder names the amount (months behind, fines and the month falling due) and where
to pay, with the member code: "Pay to Paybill 600638, account JC-4821-017." The rules'
optional `reminders` section can turn any stage off, and the rule book says so (3.7).

`GET /chamas/{id}/pay/` gives the app the same thing for the signed-in member: `owed`
(total, arrears, fines, this month, due date) and `how` (the words, the member code,
whether Pay now works).

## Pay now (Lipa na M-Pesa Online, `feeds/stk.py`)

For a chama with **its own paybill or till** whose passkey staff entered in the staff
console (Daraja channel, "Pay now passkey", stored encrypted):

1. The member taps **Pay** (`POST /chamas/{id}/pay/`, the amount defaults to what they
   owe). Safaricom shows the M-Pesa PIN prompt on their phone.
   - The account reference is their member code.
   - The amount is rounded up to whole shillings.
   - Only one prompt can be open at a time (two minutes).
2. Safaricom calls `/hooks/daraja/{token}/stk/`. If the payment went through, it is stored
   exactly as a C2B confirmation (`daraja.receive`), with the member code of the member who
   asked. It is matched, judged by the rules and receipted like every M-Pesa payment, and
   never counted twice when the same receipt also comes by C2B or on a statement.
3. The app checks `GET /pay-requests/{id}/` until it is `paid` or `failed`.

The money goes from the member's M-Pesa straight to the chama's shortcode. JiraniChama
only asks Safaricom to show the prompt. A bank's paybill (Equity 247247 and the like)
cannot be prompted, because only the owner of a shortcode can register it for this.
Kenya (KES) only for now.

## The monthly pack (1st of the month, 05:00, `monthly.py`)

After the bills (03:00), for every active chama and last month:

- **Every current member** gets their statement PDF for the month, sealed into the chain
  like any statement and in their own language. They also get a summary: what they paid,
  their savings, and what they owe.
- **The officials** get the chama's report PDF and the key numbers: the share of
  contributions paid, how many members are behind, and the bank check.
- **The treasurer** hears about JiraniChama's bill, if there is one and our paybill is set.
  One tap (`POST /bills/{id}/propose/`) proposes paying it as money out. The other
  officials approve it like any payment.

`MonthlyPack` marks a chama's month as done, so the job can run again without sending
twice.

## Not yet

- Push notifications (needs the owner's Firebase project); SMS and the app's notices
  meanwhile.
- Pay now outside Kenya (MTN MoMo, Airtel Money, M-Pesa Tanzania).
