# notify

Sends SMS. Push, USSD and SMS commands arrive in later phases.

## How sending works

`send_sms(to, text)` tries each backend in `SMS_BACKENDS`, in order, until one accepts the
message. If all fail it raises `SmsFailed`. Message text is never logged, because it can
carry a sign-in code.

| Backend | Use |
| --- | --- |
| `apps.notify.sms.ConsoleBackend` | Development and tests: keeps messages in memory |
| `apps.notify.sms.DevFileBackend` | Local development only: appends messages, codes included, to `DEV_SMS_FILE`, readable at `GET /dev/sms/?to=…` for the device end-to-end test. Refuses to load unless `DJANGO_DEBUG=1`; the URL is mounted only then |
| `apps.notify.sms.AfricasTalkingBackend` | Africa's Talking (sandbox when `AFRICASTALKING_SANDBOX=1`) |

The proposal calls for a second live provider. It is not chosen yet; adding it is one
class with a `send(to, text)` method, listed second in `SMS_BACKENDS`.

## Incoming SMS (`commands.py`)

`handle(from, text)` answers BAL, DUE, FINES, RULES, VERIFY, YES/NO and HELP (English and
Kiswahili words) for known members, within the hourly limit, and logs each message as
`InboundSms` (hash, command, replied; never the text). The hook is `SmsInboundHook`.
See [SMS commands](../flows/sms-commands.md).

## Receipts (`receipts.py`)

`send_payment_receipt` (after commit, from `reconciliation.services.apply_payment`): one
SMS per payment to members away from the app. See [Payments](../flows/payments.md).

## USSD (`ussd.py`)

`route(user, text)` answers one step of a USSD session from the path typed so far;
`handle` adds the number lookup, the hourly limit and the `UssdRequest` log. It reuses the
SMS command helpers in `commands.py`, which take an optional single membership (`only`).
See [USSD menu](../flows/ussd.md).
