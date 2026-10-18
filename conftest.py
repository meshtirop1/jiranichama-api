import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from rest_framework.test import APIClient

from apps.ledger import signing
from apps.notify.sms import ConsoleBackend


@pytest.fixture(autouse=True)
def platform_key(settings):
    """A fresh platform signing key per test, so no test depends on a key in .env."""
    key = ec.generate_private_key(ec.SECP256R1())
    settings.PLATFORM_SIGNER = "local"
    settings.PLATFORM_SIGNING_KEY_PEM = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    signing.get_signer.cache_clear()
    yield key
    signing.get_signer.cache_clear()


@pytest.fixture(autouse=True)
def media_in_tmp(settings, tmp_path):
    """Statement files go to a temporary folder, never the working tree."""
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture(autouse=True)
def sms_outbox(settings):
    settings.SMS_BACKENDS = ["apps.notify.sms.ConsoleBackend"]
    ConsoleBackend.outbox.clear()
    return ConsoleBackend.outbox


@pytest.fixture
def api():
    """An API client that arrives over HTTPS, as every real request does behind the proxy,
    so SECURE_SSL_REDIRECT stays on in tests exactly as in production."""
    return APIClient(HTTP_X_FORWARDED_PROTO="https")


@pytest.fixture
def user(db):
    from apps.accounts.models import User

    return User.objects.create_user("+254712345678", display_name="Wanjiru")


@pytest.fixture
def chama(user):
    from apps.chamas.models import Chama

    return Chama.objects.create(name="Umoja Women Group", type=Chama.Type.MERRY_GO_ROUND,
                                member_code_prefix=f"JC-{uuid.uuid4().hex[:4]}",
                                created_by=user)


@pytest.fixture
def membership(chama, user):
    from apps.chamas.models import Membership

    return Membership.objects.create(chama=chama, user=user,
                                     member_code=f"{chama.member_code_prefix}-017")
