"""Showing amounts in the chama's own currency.

Amounts are stored everywhere as whole hundredths (``*_cents``) whatever the currency, so
the ledger, the hashes and the arithmetic never change. Only how an amount is shown
depends on the currency: Uganda, Tanzania, Rwanda and Burundi do not use cents, so their
amounts are shown as whole numbers.

``fmt(cents, of)`` takes the currency from ``of``: a currency code, a Chama, or anything
with a ``chama`` (a membership, a loan, a fine...). Code that renders a whole document from
plain data (the rule book) sets the currency once with ``using(...)``.
"""

from contextlib import contextmanager
from contextvars import ContextVar

# Whether amounts are shown with cents. TZS has cents in ISO 4217 but none in practice.
SHOWS_CENTS = {
    "KES": True, "UGX": False, "TZS": False, "BIF": False, "RWF": False,
    "CDF": True, "SSP": True, "ETB": True, "SOS": True, "USD": True,
}

_current: ContextVar[str] = ContextVar("jirani_currency", default="KES")


def currency_of(of=None) -> str:
    if of is None:
        return _current.get()
    if isinstance(of, str):
        return of
    code = getattr(of, "currency", None)
    if isinstance(code, str) and code:
        return code
    chama = getattr(of, "chama", None)
    if chama is not None:
        return currency_of(chama)
    return _current.get()


def number(cents: int, currency: str | None = None) -> str:
    """The amount without the currency code: "1,000" or "1,000.50"."""
    code = currency or _current.get()
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(int(cents)), 100)
    if SHOWS_CENTS.get(code, True) and part:
        return f"{sign}{whole:,}.{part:02d}"
    return f"{sign}{whole:,}"


def fmt(cents: int, of=None) -> str:
    """ "KES 1,000", "UGX 50,000", "USD 12.50"."""
    code = currency_of(of)
    return f"{code} {number(cents, code)}"


@contextmanager
def using(of):
    token = _current.set(currency_of(of))
    try:
        yield
    finally:
        _current.reset(token)
