"""Pay now (2026-10-21): the M-Pesa PIN prompt on the member's phone, for a chama with its
own paybill. The money goes straight to the chama's shortcode; Safaricom's answer is
recorded like any M-Pesa payment and never counted twice."""

import base64

import pytest
from django.utils import timezone

from apps.feeds import daraja, stk
from apps.feeds.models import BankTransaction, DarajaChannel, StkRequest
from apps.ledger.models import LedgerEntry

from .phase1 import active_chama

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


class _Resp:
    def __init__(self, data):
        self._data = data
        self.content = b"x"

    def json(self):
        return self._data


@pytest.fixture
def chama(monkeypatch):
    c, (chair, treasurer, secretary) = active_chama(since=TODAY)
    channel = DarajaChannel.objects.create(chama=c, shortcode="600638")
    monkeypatch.setattr(daraja, "_token", lambda: "TOKEN")
    return c, channel, secretary


def _callback(api, channel, checkout, *, code=0, receipt="QKL1234567", amount=1000):
    api.force_authenticate(None)
    cb = {"MerchantRequestID": "m1", "CheckoutRequestID": checkout, "ResultCode": code,
          "ResultDesc": "ok" if code == 0 else "Request cancelled by user"}
    if code == 0:
        cb["CallbackMetadata"] = {"Item": [
            {"Name": "Amount", "Value": amount}, {"Name": "MpesaReceiptNumber", "Value": receipt},
            {"Name": "TransactionDate", "Value": int(TODAY.strftime("%Y%m%d") + "101500")},
            {"Name": "PhoneNumber", "Value": 254711000111}]}
    return api.post(f"/api/v1/hooks/daraja/{channel.token}/stk/", {"Body": {"stkCallback": cb}},
                    format="json")


def test_without_the_passkey_members_pay_the_usual_way(chama, api):
    c, channel, member = chama
    api.force_authenticate(member.user)
    assert api.get(f"/api/v1/chamas/{c.id}/pay/").data["how"]["pay_now"] is False
    r = api.post(f"/api/v1/chamas/{c.id}/pay/", {}, format="json")
    assert r.status_code == 409 and r.data["code"] == "pay_now_unavailable"


def test_pay_now_sends_the_prompt_and_records_the_payment_once(chama, api, monkeypatch):
    c, channel, member = chama
    channel.set_stk_passkey("PASSKEY")
    channel.save()
    sent = {}

    def post(url, json, headers, timeout):
        sent.update(url=url, body=json, auth=headers["Authorization"])
        return _Resp({"ResponseCode": "0", "CheckoutRequestID": "ws_CO_1",
                      "MerchantRequestID": "m1"})

    monkeypatch.setattr(stk.requests, "post", post)
    api.force_authenticate(member.user)
    assert api.get(f"/api/v1/chamas/{c.id}/pay/").data["how"]["pay_now"] is True
    r = api.post(f"/api/v1/chamas/{c.id}/pay/", {"amount_cents": 99_950}, format="json")
    assert r.status_code == 201 and r.data["amount_cents"] == 100_000  # whole shillings
    body = sent["body"]
    assert sent["url"].endswith("/mpesa/stkpush/v1/processrequest")
    assert sent["auth"] == "Bearer TOKEN"
    assert body["Amount"] == 1000 and body["AccountReference"] == member.member_code
    assert body["PartyA"] == member.user.phone.lstrip("+")
    assert base64.b64decode(body["Password"]).decode() == f"600638PASSKEY{body['Timestamp']}"
    assert body["CallBackURL"].endswith(f"/hooks/daraja/{channel.token}/stk/")
    # A second tap while the prompt is open is refused.
    assert api.post(f"/api/v1/chamas/{c.id}/pay/", {}, format="json").status_code == 429

    assert _callback(api, channel, "ws_CO_1").status_code == 200
    req = StkRequest.objects.get()
    assert req.state == "paid" and req.receipt == "QKL1234567"
    entry = LedgerEntry.objects.get(chama=c, type="contribution")
    assert entry.member_id == member.id and entry.amount_cents == 100_000
    # The same answer again, or the same payment as a C2B confirmation: counted once.
    _callback(api, channel, "ws_CO_1")
    daraja.receive(channel, {"TransID": "QKL1234567", "BusinessShortCode": "600638",
                             "TransAmount": "1000", "BillRefNumber": member.member_code,
                             "TransTime": TODAY.strftime("%Y%m%d") + "101500"})
    assert BankTransaction.objects.filter(chama=c).count() == 1
    assert LedgerEntry.objects.filter(chama=c, type="contribution").count() == 1
    api.force_authenticate(member.user)
    assert api.get(f"/api/v1/pay-requests/{req.id}/").data["state"] == "paid"


def test_a_cancelled_prompt_records_nothing(chama, api, monkeypatch):
    c, channel, member = chama
    channel.set_stk_passkey("PASSKEY")
    channel.save()
    monkeypatch.setattr(stk.requests, "post", lambda *a, **k: _Resp(
        {"ResponseCode": "0", "CheckoutRequestID": "ws_CO_2"}))
    stk.start(member, 100_000)
    _callback(api, channel, "ws_CO_2", code=1032)
    assert StkRequest.objects.get().state == "failed"
    assert not BankTransaction.objects.filter(chama=c).exists()


def test_m_pesa_refusing_the_prompt_is_said_plainly(chama, monkeypatch):
    c, channel, member = chama
    channel.set_stk_passkey("PASSKEY")
    channel.save()
    monkeypatch.setattr(stk.requests, "post", lambda *a, **k: _Resp(
        {"errorCode": "400.002.02", "errorMessage": "Bad Request - Invalid PhoneNumber"}))
    with pytest.raises(Exception) as e:
        stk.start(member, 100_000)
    assert getattr(e.value, "problem_code", "") == "mpesa_refused"
    assert StkRequest.objects.get().state == "refused"
