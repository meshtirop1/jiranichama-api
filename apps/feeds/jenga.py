"""Equity Bank's Jenga API: account balance and full statement (read-only). See
docs/flows/bank-feeds.md.

The request and response shapes follow the Jenga v3 documentation. They are NOT yet
checked against a live account: run a UAT pull with real credentials before switching a
chama's feed on. Every request is signed with the account holder's RSA private key
(SHA-256), as Jenga requires.
"""

import base64
from datetime import date
from decimal import Decimal, InvalidOperation

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.conf import settings


class JengaError(Exception):
    pass


def _cents(raw) -> int:
    try:
        return int((Decimal(str(raw).replace(",", "")) * 100).to_integral_value())
    except (InvalidOperation, ValueError):
        raise JengaError(f"not an amount: {raw!r}") from None


def _sign(private_key_pem: str, message: str) -> str:
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    signature = key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(signature).decode()


class Client:
    def __init__(self, creds: dict, *, base_url: str | None = None, session=None):
        missing = {"api_key", "merchant_code", "consumer_secret", "private_key_pem"} - set(creds)
        if missing:
            raise JengaError(f"missing credentials: {', '.join(sorted(missing))}")
        self.creds = creds
        self.base = (base_url or settings.JENGA_BASE_URL).rstrip("/")
        self.http = session or requests
        self._token = None

    def _post(self, path, **kw):
        r = self.http.post(f"{self.base}{path}", timeout=20, **kw)
        if r.status_code >= 400:
            raise JengaError(f"{path}: HTTP {r.status_code}")
        return r.json()

    def token(self) -> str:
        if self._token is None:
            data = self._post("/authentication/api/v3/authenticate/merchant",
                              headers={"Api-Key": self.creds["api_key"]},
                              json={"merchantCode": self.creds["merchant_code"],
                                    "consumerSecret": self.creds["consumer_secret"]})
            self._token = data.get("accessToken")
            if not self._token:
                raise JengaError("no access token")
        return self._token

    def _headers(self, signed: str) -> dict:
        return {"Authorization": f"Bearer {self.token()}",
                "signature": _sign(self.creds["private_key_pem"], signed)}

    def balance_cents(self, country: str, account: str) -> int:
        path = f"/v3-apis/account-api/v3.0/accounts/balances/{country}/{account}"
        r = self.http.get(f"{self.base}{path}", timeout=20,
                          headers=self._headers(f"{country}{account}"))
        if r.status_code >= 400:
            raise JengaError(f"balance: HTTP {r.status_code}")
        balances = (r.json().get("data") or {}).get("balances") or []
        for b in balances:
            if str(b.get("type", "")).lower() == "current":
                return _cents(b.get("amount"))
        if balances:
            return _cents(balances[0].get("amount"))
        raise JengaError("no balance in the answer")

    def statement(self, country: str, account: str, start: date, end: date) -> list[dict]:
        """Lines as dicts: value_date, reference, narration, direction, amount_cents,
        balance_cents."""
        to = end.isoformat()
        data = self._post("/v3-apis/account-api/v3.0/accounts/fullstatement",
                          headers=self._headers(f"{account}{country}{to}"),
                          json={"countryCode": country, "accountNumber": account,
                                "fromDate": start.isoformat(), "toDate": to,
                                "limit": settings.JENGA_STATEMENT_LIMIT})
        out = []
        for i, t in enumerate((data.get("data") or {}).get("transactions") or []):
            kind = str(t.get("type", "")).lower()
            raw_date = str(t.get("date", ""))[:10]
            try:
                value_date = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise JengaError(f"bad date {raw_date!r}") from exc
            amount = abs(_cents(t.get("amount")))
            ref = str(t.get("reference") or t.get("serial") or "").strip()
            out.append({
                "value_date": value_date,
                "reference": ref or f"{raw_date}:{t.get('serial', i)}:{amount}",
                "narration": str(t.get("description") or "").strip(),
                "direction": "credit" if kind.startswith("c") else "debit",
                "amount_cents": amount,
                "balance_cents": (_cents(t["runningBalance"])
                                  if t.get("runningBalance") not in (None, "") else None),
            })
        return out
