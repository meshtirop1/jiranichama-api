# SMS commands and votes from basic phones (4.9, 5.11)

Members without a smartphone text a word to the chama shortcode and get the answer by
SMS. The provider (Africa's Talking) posts each incoming message to
`/api/v1/hooks/sms/{SMS_INBOUND_TOKEN}/inbound/` (`from`, `text`, `id`). An empty token
means the hook is off; `SMS_INBOUND_ALLOWED_IPS` can narrow the callers.

## Commands

| English | Kiswahili | Answer, for each of the member's chamas (up to 3) |
| --- | --- | --- |
| BAL | SALIO | savings, contributions behind, fines owed |
| DUE | DENI | what is due next and by when, anything behind, and the member code to pay with |
| FINES | FAINI | how many unpaid fines and the total |
| RULES | KANUNI | the monthly contribution, due day, grace days and late fine |
| VERIFY code | HAKIKI code | a receipt (8 characters) in the member's own chamas, or a statement check code (10) |
| YES n / NO n | NDIYO n / HAPANA n | a vote (below) |
| HELP | MSAADA | the list (also the answer to anything unknown) |

- **Language**: a Kiswahili word gets a Kiswahili answer, an English word an English one;
  anything else uses the member's own language.
- **Read-only**: nothing but voting changes records.
- **Who is asking**: the sender's number, hashed with our key, finds the member. Unknown
  numbers get no answer, so we never text strangers.
- **Limit**: each number may send `SMS_COMMANDS_PER_HOUR` messages an hour (default 10);
  beyond that there is no answer. Every message is logged (`InboundSms`) as the hash, the
  command word and whether we answered. The text itself is never stored.
- **Receipts are private**: VERIFY finds a receipt only in the sender's own chamas.
  Statement codes are public, as on the check page.

## Voting by SMS

When a vote opens (for example a payout above the vote level), every eligible member
**without a phone key** gets the question by SMS with **their own 4-digit number**:
"Umoja vote: Pay Mary KES 60,000 for Hospital bill? Reply YES 4821 or NO 4821 by 22 Oct
10:14".
- A reply counts only from the member's own number, and the number names exactly one of
  their votes.
- The first ballot counts, whether it came from the app, an SMS code or a reply.
- Members with a phone key are told to vote in the app.
- Ballots cast this way are recorded with method `reply`.

The provider vouches for the sender's number on incoming SMS. That is the same trust
level as an SMS code, so a reply is a valid ballot for basic-phone members.

## Not yet

USSD (Phase 4), the LOAN command (loans arrive in Phase 3), and joining a chama by SMS
(5.2a).
