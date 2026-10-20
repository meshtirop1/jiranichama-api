"""The countries JiraniChama serves: phone numbers, currency, languages and time zone.

Kenya, Uganda and Tanzania are open (Wave 1, owner decision 2026-10-20). The others are
listed so people can see they are coming, but cannot be chosen yet: Rwanda, Ethiopia and
DR Congo need their data hosted in the country or a regulator's approval first, Burundi
needs a sponsor registered with its central bank, and Somalia and South Sudan wait for
USD and Arabic support. See docs/flows/countries.md.
"""

import re
from dataclasses import dataclass, field

# Every language the app can be set to. Those not in TRANSLATED fall back to English for
# server texts (SMS, rule book, PDFs) until a reviewed translation exists.
LANGUAGES = {
    "en": "English", "sw": "Kiswahili", "fr": "Français", "so": "Soomaali",
    "lg": "Luganda", "nyn": "Runyankore", "rw": "Ikinyarwanda", "rn": "Ikirundi",
    "ln": "Lingála", "lua": "Tshiluba", "ar": "العربية", "am": "አማርኛ",
    "om": "Afaan Oromoo", "ti": "ትግርኛ",
}
TRANSLATED = ("en", "sw")
LANGUAGE_CHOICES = list(LANGUAGES.items())
TRANSLATED_CHOICES = [(k, LANGUAGES[k]) for k in TRANSLATED]


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    dial: str  # "+254"
    mobile: str  # a regex for the national mobile number, without the leading 0
    currency: str
    currencies: tuple[str, ...]
    languages: tuple[str, ...]
    timezone: str
    open: bool
    example: str  # a national example shown in hints
    regex: re.Pattern = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "regex", re.compile(rf"^(?:{self.mobile})$"))


COUNTRIES: dict[str, Country] = {c.code: c for c in [
    Country("KE", "Kenya", "+254", r"[17]\d{8}", "KES", ("KES",), ("en", "sw", "so"),
            "Africa/Nairobi", True, "0712 345 678"),
    Country("UG", "Uganda", "+256", r"7\d{8}", "UGX", ("UGX",), ("en", "lg", "sw", "nyn"),
            "Africa/Kampala", True, "0772 123 456"),
    Country("TZ", "Tanzania", "+255", r"[67]\d{8}", "TZS", ("TZS",), ("sw", "en"),
            "Africa/Dar_es_Salaam", True, "0754 123 456"),
    Country("RW", "Rwanda", "+250", r"7\d{8}", "RWF", ("RWF",), ("rw", "en", "fr", "sw"),
            "Africa/Kigali", False, "0788 123 456"),
    Country("BI", "Burundi", "+257", r"[67]\d{7}", "BIF", ("BIF",), ("rn", "fr", "sw"),
            "Africa/Bujumbura", False, "79 123 456"),
    Country("CD", "DR Congo", "+243", r"[89]\d{8}", "CDF", ("CDF", "USD"),
            ("fr", "ln", "sw", "lua"), "Africa/Lubumbashi", False, "0812 345 678"),
    Country("SS", "South Sudan", "+211", r"9\d{8}", "SSP", ("SSP", "USD"), ("en", "ar"),
            "Africa/Juba", False, "0912 345 678"),
    Country("ET", "Ethiopia", "+251", r"[79]\d{8}", "ETB", ("ETB",), ("am", "om", "ti", "en"),
            "Africa/Addis_Ababa", False, "0911 234 567"),
    Country("SO", "Somalia", "+252", r"[1-9]\d{7,8}", "USD", ("USD", "SOS"), ("so", "ar", "en"),
            "Africa/Mogadishu", False, "061 234 5678"),
]}
DEFAULT_COUNTRY = "KE"
OPEN = tuple(c for c, x in COUNTRIES.items() if x.open)


def get(code: str | None) -> Country:
    return COUNTRIES.get((code or DEFAULT_COUNTRY).upper(), COUNTRIES[DEFAULT_COUNTRY])


def country_of_e164(e164: str) -> Country | None:
    for c in COUNTRIES.values():
        if e164.startswith(c.dial):
            return c
    return None


def as_dict(c: Country) -> dict:
    return {"code": c.code, "name": c.name, "dial": c.dial, "currency": c.currency,
            "currencies": list(c.currencies), "languages": list(c.languages),
            "timezone": c.timezone, "open": c.open, "example": c.example}
