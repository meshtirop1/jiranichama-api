import re

from apps.core.crypto import keyed_hash

_KENYA = re.compile(r"^(?:\+?254|0)?([17]\d{8})$")


class InvalidPhone(ValueError):
    pass


def normalise_phone(raw: str) -> str:
    """Return a Kenyan mobile number as +254XXXXXXXXX. Accepts 07…, 01…, 2547…, +2547…"""
    digits = re.sub(r"[\s\-()]", "", raw or "")
    match = _KENYA.match(digits)
    if not match:
        raise InvalidPhone("Enter a Kenyan mobile number, for example 0712 345 678.")
    return f"+254{match.group(1)}"


def phone_hash(e164: str) -> str:
    return keyed_hash("phone", e164)


def phone_hint(e164: str) -> str:
    """What we show back to a person: +254 7•• ••• 678."""
    return f"{e164[:5]}••• ••• {e164[-3:]}"
