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
