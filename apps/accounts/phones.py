import re

from apps.core import countries
from apps.core.crypto import keyed_hash


class InvalidPhone(ValueError):
    pass


def normalise_phone(raw: str, country: str | None = None) -> str:
    """Return a mobile number in international form (+254712345678).

    A number written with its country code (+256…, 256…) is read in that country. One
    written the local way (07…, 7…) is read in ``country`` (default Kenya), so a Kenyan
    number gives exactly the same result as before countries were added and existing
    accounts are still found.
    """
    digits = re.sub(r"[\s\-().]", "", raw or "")
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    for c in countries.COUNTRIES.values():
        code = c.dial[1:]
        if digits.startswith("+" + code) or (digits.startswith(code)
                                             and len(digits) > len(code) + 6):
            national = digits[len(code) + (1 if digits.startswith("+") else 0):]
            national = national[1:] if national.startswith("0") else national
            if c.regex.match(national):
                return c.dial + national
            raise InvalidPhone(f"Enter a {c.name} mobile number, for example {c.example}.")
    home = countries.get(country)
    national = digits[1:] if digits.startswith("0") else digits
    if home.regex.match(national):
        return home.dial + national
    raise InvalidPhone(f"Enter a {home.name} mobile number, for example {home.example}.")


def phone_hash(e164: str) -> str:
    return keyed_hash("phone", e164)


def phone_hint(e164: str) -> str:
    """What we show back to a person: +2547••• ••• 678."""
    c = countries.country_of_e164(e164)
    head = len(c.dial) + 1 if c else 5
    return f"{e164[:head]}••• ••• {e164[-3:]}"
