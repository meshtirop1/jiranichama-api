"""Canonical JSON (RFC 8785, JCS) for the values JiraniChama hashes and signs.

The Dart app has a byte-for-byte twin of this module; both are held to the shared
vectors in ``vectors/``. We support a deliberately small domain: objects with string
keys, arrays, strings, booleans, null and integers within the I-JSON safe range.
Floats are refused because money is always integer cents, and refusing them removes the
hardest part of RFC 8785 (number formatting) from both implementations.
"""

from datetime import UTC, datetime

MAX_SAFE_INTEGER = 2**53 - 1

_SHORT_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


class CanonicalError(ValueError):
    pass


def canonical_json(value) -> str:
    out: list[str] = []
    _encode(value, out)
    return "".join(out)


def canonical_bytes(value) -> bytes:
    return canonical_json(value).encode("utf-8")


def iso_ms(dt: datetime) -> str:
    """UTC ISO 8601, truncated to milliseconds, with a Z: 2026-10-18T06:30:00.123Z."""
    if dt.tzinfo is None:
        raise CanonicalError("timestamps must be timezone-aware")
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def truncate_ms(dt: datetime) -> datetime:
    return dt.replace(microsecond=(dt.microsecond // 1000) * 1000)


def _encode(value, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            raise CanonicalError(f"integer outside the safe range: {value}")
        out.append(str(value))
    elif isinstance(value, float):
        raise CanonicalError("floats are not allowed; use integer cents")
    elif isinstance(value, str):
        _encode_string(value, out)
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for i, item in enumerate(value):
            if i:
                out.append(",")
            _encode(item, out)
        out.append("]")
    elif isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                raise CanonicalError(f"object keys must be strings, got {type(key).__name__}")
        out.append("{")
        # RFC 8785 sorts keys by their UTF-16 code units, not by code points.
        for i, key in enumerate(sorted(value, key=lambda k: k.encode("utf-16-be"))):
            if i:
                out.append(",")
            _encode_string(key, out)
            out.append(":")
            _encode(value[key], out)
        out.append("}")
    else:
        raise CanonicalError(f"cannot canonicalise {type(value).__name__}")


def _encode_string(s: str, out: list[str]) -> None:
    try:
        s.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CanonicalError("strings must be valid Unicode (no lone surrogates)") from exc
    out.append('"')
    for ch in s:
        if ch in _SHORT_ESCAPES:
            out.append(_SHORT_ESCAPES[ch])
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
