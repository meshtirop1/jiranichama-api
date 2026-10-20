"""Smile ID Enhanced KYC: the ID number is looked up in the country's ID registry, server
to server. https://docs.usesmileid.com (job type 5, /v1/id_verification).

Switched off until the owner adds credentials (SMILE_ID_PARTNER_ID, SMILE_ID_API_KEY).
Then ``available()`` is true and the app shows "Check my ID". The sandbox is used unless
SMILE_ID_PRODUCTION is set.
"""

import base64
import hashlib
import hmac
from datetime import UTC, datetime

import requests
from django.conf import settings

SANDBOX = "https://testapi.smileidentity.com/v1"
PRODUCTION = "https://api.smileidentity.com/v1"

# Smile ID's ResultCode for an Enhanced KYC lookup.
VERIFIED = {"1012"}  # "Valid ID" (exact match in the registry)
NOT_FOUND = {"1013"}  # "ID number not found"

# The ID types Smile ID accepts per open country (Enhanced KYC).
ID_TYPES = {
    "KE": ["NATIONAL_ID", "ALIEN_CARD", "PASSPORT"],
    "UG": ["NATIONAL_ID_NO_PHOTO"],
    "TZ": ["NIN"],
}


def available() -> bool:
    return bool(getattr(settings, "SMILE_ID_PARTNER_ID", "")
                and getattr(settings, "SMILE_ID_API_KEY", ""))


def _signature(timestamp: str) -> str:
    msg = (timestamp + settings.SMILE_ID_PARTNER_ID + "sid_request").encode()
    return base64.b64encode(hmac.new(settings.SMILE_ID_API_KEY.encode(), msg,
                                     hashlib.sha256).digest()).decode()


def lookup(*, country: str, id_type: str, id_number: str, first_name: str, last_name: str,
           job_id: str, user_id: str) -> dict:
    """Returns {"code", "verified", "full_name"}. Raises requests errors upward."""
    timestamp = datetime.now(UTC).isoformat()
    base = PRODUCTION if getattr(settings, "SMILE_ID_PRODUCTION", False) else SANDBOX
    r = requests.post(f"{base}/id_verification", timeout=30, json={
        "source_sdk": "rest_api", "source_sdk_version": "1.0.0",
        "partner_id": settings.SMILE_ID_PARTNER_ID, "timestamp": timestamp,
        "signature": _signature(timestamp), "country": country, "id_type": id_type,
        "id_number": id_number, "first_name": first_name, "last_name": last_name,
        "partner_params": {"job_id": job_id, "user_id": user_id, "job_type": 5},
    })
    r.raise_for_status()
    body = r.json()
    code = str(body.get("ResultCode", ""))
    return {"code": code, "verified": code in VERIFIED,
            "full_name": str(body.get("FullName") or "").strip()}
