"""Field encryption and keyed hashes.

Phase 0 uses Fernet keys from the environment (MultiFernet, newest first, so keys can be
rotated). Production moves the data keys under KMS envelope encryption without changing
callers: everything goes through ``encrypt`` / ``decrypt`` / ``keyed_hash``.
"""

import hashlib
import hmac
from functools import cache

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings


@cache
def _fernet() -> MultiFernet:
    keys = [k for k in settings.FIELD_ENCRYPTION_KEYS if k]
    if not keys:
        raise RuntimeError("FIELD_ENCRYPTION_KEYS is not set")
    return MultiFernet([Fernet(k.encode()) for k in keys])


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(token: bytes) -> str:
    return _fernet().decrypt(bytes(token)).decode("utf-8")


def keyed_hash(purpose: str, value: str) -> str:
    """HMAC-SHA256 of ``value``, separated by purpose so one hash cannot stand in for
    another (a phone hash is never valid as an ID-number hash)."""
    if not settings.HMAC_KEY:
        raise RuntimeError("HMAC_KEY is not set")
    msg = f"{purpose}\x1f{value}".encode()
    return hmac.new(settings.HMAC_KEY.encode(), msg, hashlib.sha256).hexdigest()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
