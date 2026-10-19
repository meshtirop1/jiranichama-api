"""SMS through two providers with automatic fallback.

Backends are tried in the order of ``settings.SMS_BACKENDS``; the first one that accepts
the message wins. Message text is never logged, because it can carry a sign-in code.
"""

import logging
from dataclasses import dataclass

import requests
from django.conf import settings
from django.utils.module_loading import import_string

log = logging.getLogger(__name__)


@dataclass
class SmsResult:
    ok: bool
    provider: str
    provider_id: str = ""
    error: str = ""


class SmsFailed(RuntimeError):
    pass


class ConsoleBackend:
    """Development and tests: keeps messages in memory instead of sending them."""

    name = "console"
    outbox: list[tuple[str, str]] = []

    def send(self, to: str, text: str) -> SmsResult:
        ConsoleBackend.outbox.append((to, text))
        log.info("SMS (console) to %s, %d characters", to[-3:], len(text))
        return SmsResult(ok=True, provider=self.name)


class DevFileBackend:
    """Local development only: appends every message, code included, to
    ``settings.DEV_SMS_FILE`` so an emulator test can read sign-in codes. Refuses to
    load unless DEBUG is on and the file is configured."""

    name = "dev_file"

    def __init__(self):
        if not settings.DEBUG or not settings.DEV_SMS_FILE:
            raise RuntimeError("DevFileBackend is for local development only")

    def send(self, to: str, text: str) -> SmsResult:
        import json

        with open(settings.DEV_SMS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"to": to, "text": text}) + "\n")
        return SmsResult(ok=True, provider=self.name)


class AfricasTalkingBackend:
    name = "africastalking"
    timeout = 10

    @property
    def url(self) -> str:
        host = "api.sandbox.africastalking.com" if settings.AFRICASTALKING_SANDBOX \
            else "api.africastalking.com"
        return f"https://{host}/version1/messaging"

    def send(self, to: str, text: str) -> SmsResult:
        data = {"username": settings.AFRICASTALKING_USERNAME, "to": to, "message": text}
        if settings.AFRICASTALKING_SENDER_ID:
            data["from"] = settings.AFRICASTALKING_SENDER_ID
        try:
            resp = requests.post(
                self.url,
                data=data,
                headers={"apiKey": settings.AFRICASTALKING_API_KEY, "Accept": "application/json"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            recipients = resp.json()["SMSMessageData"]["Recipients"]
            first = recipients[0] if recipients else {}
        except (requests.RequestException, KeyError, ValueError, IndexError) as exc:
            return SmsResult(ok=False, provider=self.name, error=type(exc).__name__)
        if first.get("status") == "Success":
            return SmsResult(ok=True, provider=self.name, provider_id=first.get("messageId", ""))
        return SmsResult(ok=False, provider=self.name, error=str(first.get("status", "unknown")))


def _backends():
    return [import_string(path)() for path in settings.SMS_BACKENDS]


def send_sms(to: str, text: str) -> SmsResult:
    failures = []
    for backend in _backends():
        result = backend.send(to, text)
        if result.ok:
            return result
        log.warning("SMS provider %s failed: %s", result.provider, result.error)
        failures.append(f"{result.provider}: {result.error}")
    raise SmsFailed("; ".join(failures) or "no SMS backends configured")
