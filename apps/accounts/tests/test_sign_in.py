import re
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import OtpChallenge, User
from apps.accounts.phones import InvalidPhone, normalise_phone

pytestmark = pytest.mark.django_db

PHONE = "0712 345 678"
E164 = "+254712345678"


def _code(sms_outbox):
    to, text = sms_outbox[-1]
    assert to == E164
    return re.search(r"\b(\d{6})\b", text).group(1)


@pytest.mark.parametrize("raw", ["0712345678", "+254712345678", "254712345678",
                                 "0712 345-678", "712345678"])
def test_normalise_accepts_kenyan_formats(raw):
    assert normalise_phone(raw) == E164


@pytest.mark.parametrize("raw", ["", "0812345678", "+255712345678", "07123456"])
def test_normalise_rejects_others(raw):
    with pytest.raises(InvalidPhone):
        normalise_phone(raw)


def test_sign_in_creates_user_and_returns_tokens(api, sms_outbox):
    r = api.post("/api/v1/auth/otp/", {"phone": PHONE}, format="json")
    assert r.status_code == 202, r.content
    assert r.json()["phone_hint"].endswith("678")

    r = api.post("/api/v1/auth/verify/", {"phone": PHONE, "code": _code(sms_outbox)},
                 format="json")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["is_new"] is True and body["user"]["has_pin"] is False
    assert body["access"] and body["refresh"]

    user = User.objects.get()
    assert user.phone == E164
    assert E164 not in user.phone_hash and bytes(user.phone_enc) != E164.encode()


def test_code_is_never_stored_in_plain_text(api, sms_outbox):
    api.post("/api/v1/auth/otp/", {"phone": PHONE}, format="json")
    code = _code(sms_outbox)
    challenge = OtpChallenge.objects.get()
    assert code not in challenge.code_hmac


def test_wrong_code_counts_attempts_then_locks(api, sms_outbox, settings):
    api.post("/api/v1/auth/otp/", {"phone": PHONE}, format="json")
    for left in range(settings.OTP_MAX_ATTEMPTS - 1, -1, -1):
        r = api.post("/api/v1/auth/verify/", {"phone": PHONE, "code": "000000"}, format="json")
        assert r.status_code == 400
        assert r["Content-Type"] == "application/problem+json"
        assert r.json()["code"] == "otp_wrong" and r.json()["attempts_left"] == left
    r = api.post("/api/v1/auth/verify/", {"phone": PHONE, "code": _code(sms_outbox)},
                 format="json")
    assert r.json()["code"] == "otp_locked"


def test_expired_code_is_refused(api, sms_outbox):
    api.post("/api/v1/auth/otp/", {"phone": PHONE}, format="json")
    OtpChallenge.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    r = api.post("/api/v1/auth/verify/", {"phone": PHONE, "code": _code(sms_outbox)},
                 format="json")
    assert r.json()["code"] == "otp_expired"


def test_code_works_only_once(api, sms_outbox):
    api.post("/api/v1/auth/otp/", {"phone": PHONE}, format="json")
    code = _code(sms_outbox)
    assert api.post("/api/v1/auth/verify/", {"phone": PHONE, "code": code},
                    format="json").status_code == 200
    r = api.post("/api/v1/auth/verify/", {"phone": PHONE, "code": code}, format="json")
    assert r.json()["code"] == "otp_expired"


def test_codes_are_rate_limited_per_phone(api, settings):
    for _ in range(settings.OTP_MAX_PER_WINDOW):
        assert api.post("/api/v1/auth/otp/", {"phone": PHONE}, format="json").status_code == 202
    r = api.post("/api/v1/auth/otp/", {"phone": PHONE}, format="json")
    assert r.status_code == 429 and r.json()["code"] == "otp_rate_limited"


def test_invalid_phone_is_a_problem_document(api):
    r = api.post("/api/v1/auth/otp/", {"phone": "12"}, format="json")
    assert r.status_code == 400 and r.json()["code"] == "invalid"
    assert "phone" in r.json()["errors"]


def test_set_pin_then_change_it(api, user):
    api.force_authenticate(user)
    assert api.post("/api/v1/auth/pin/", {"pin": "123456"}, format="json").status_code == 400
    assert api.post("/api/v1/auth/pin/", {"pin": "482916"}, format="json").status_code == 204
    r = api.post("/api/v1/auth/pin/", {"pin": "731905", "current_pin": "000001"}, format="json")
    assert r.status_code == 403
    r = api.post("/api/v1/auth/pin/", {"pin": "731905", "current_pin": "482916"}, format="json")
    assert r.status_code == 204
    user.refresh_from_db()
    assert user.check_pin("731905") and "731905" not in user.pin_hash


def test_plain_http_is_redirected():
    from rest_framework.test import APIClient

    r = APIClient().post("/api/v1/auth/otp/", {"phone": PHONE}, format="json")
    assert r.status_code == 301


def test_anonymise_removes_personal_details(user):
    user.anonymise()
    user.refresh_from_db()
    assert user.phone == "" and user.display_name == "" and not user.is_active
    assert user.phone_hash == f"anonymised:{user.id}"
