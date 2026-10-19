"""M-Pesa paybill notifications (Daraja C2B): matched the same minute, never counted twice
when the bank statement arrives (5.3, 6.9)."""

import base64
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.feeds import daraja
from apps.feeds import services as feeds
from apps.feeds.models import BankTransaction, DarajaChannel
from apps.ledger.models import LedgerEntry
from apps.reconciliation import daily

from .phase1 import active_chama, statement_csv

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


@pytest.fixture(autouse=True)
def mail(settings):
    settings.BANK_STATEMENT_SENDERS = {"equity": ["equitybank.example"]}


def _payload(trans_id, amount, ref, msisdn="254799000123", day=TODAY):
    return {"TransactionType": "Pay Bill", "TransID": trans_id,
            "TransTime": day.strftime("%Y%m%d") + "093015", "TransAmount": str(amount),
            "BusinessShortCode": "600638", "BillRefNumber": ref, "InvoiceNumber": "",
            "OrgAccountBalance": "", "ThirdPartyTransID": "", "MSISDN": msisdn,
            "FirstName": "WANJIRU", "MiddleName": "", "LastName": "K"}


def _hook(api, channel, payload, step="confirmation"):
    api.force_authenticate(None)
    return api.post(f"/api/v1/hooks/daraja/{channel.token}/{step}/", payload, format="json")


def _statement(chama, lines, opening_cents, name="s.csv"):
    data = statement_csv(lines, opening_cents=opening_cents)
    [s] = feeds.receive_mail({
        "to": chama.statement_inbox.address, "from": "s@equitybank.example",
        "auth": {"spf": "pass", "dkim": "pass", "dmarc": "pass",
                 "dkim_domain": "equitybank.example"},
        "attachments": [{"filename": name, "content_b64": base64.b64encode(data).decode()}]})
    assert s.state == "accepted", s.problems
    return s


@pytest.fixture
def setup():
    c, (chair, treasurer, secretary) = active_chama(since=TODAY)
    channel = DarajaChannel.objects.create(chama=c, shortcode="600638")
    return c, channel, chair, treasurer


def test_a_paybill_payment_is_recorded_the_same_minute(api, setup):
    c, channel, chair, _ = setup
    r = _hook(api, channel, {"TransID": "X"}, step="validation")
    assert r.status_code == 200 and r.json() == {"ResultCode": 0, "ResultDesc": "Accepted"}

    r = _hook(api, channel, _payload("RKTQDM7W6S", "1000.00", chair.member_code))
    assert r.json()["ResultCode"] == 0
    txn = BankTransaction.objects.get(source="daraja", external_ref="RKTQDM7W6S")
    assert txn.state == "recorded" and txn.amount_cents == 100_000
    assert LedgerEntry.objects.filter(source_id=txn.id, type="contribution",
                                      member=chair).exists()

    _hook(api, channel, _payload("RKTQDM7W6S", "1000.00", chair.member_code))  # resent
    assert BankTransaction.objects.filter(external_ref="RKTQDM7W6S").count() == 1


def test_the_payer_phone_matches_when_the_reference_does_not(api, setup):
    c, channel, chair, _ = setup
    from apps.accounts.phones import normalise_phone
    phone = normalise_phone(chair.user.phone)
    _hook(api, channel, _payload("RKTQDM7W6T", "500", "JANE", msisdn=phone.lstrip("+")))
    assert BankTransaction.objects.get(external_ref="RKTQDM7W6T").state == "recorded"

    # A masked number matches nobody: the treasurer's queue.
    _hook(api, channel, _payload("RKTQDM7W6U", "500", "JANE", msisdn="2547****123"))
    assert BankTransaction.objects.get(external_ref="RKTQDM7W6U").state == "unmatched"


def test_a_wrong_token_is_not_found(api, setup):
    c, channel, chair, _ = setup
    api.force_authenticate(None)
    r = api.post("/api/v1/hooks/daraja/not-the-token/confirmation/",
                 _payload("RKTQDM7W6V", "500", chair.member_code), format="json")
    assert r.status_code == 404
    assert not BankTransaction.objects.filter(external_ref="RKTQDM7W6V").exists()


def test_the_statement_line_for_the_same_payment_is_a_duplicate(api, setup):
    c, channel, chair, _ = setup
    _statement(c, [(TODAY - timedelta(days=1), "DEPOSIT", 0, 500_000)], 0, "a.csv")
    _hook(api, channel, _payload("RKTQDM7W6S", "1000", chair.member_code))
    _hook(api, channel, _payload("RKTQDM7W6W", "300", chair.member_code))
    # Before the statement shows them, the bank figure does not include them either.
    daily.reconcile(c)
    assert daily.summary(c)["gap_cents"] == 500_000  # only the earlier unmatched deposit

    _statement(c, [
        (TODAY, f"MPESA C2B RKTQDM7W6S {chair.member_code}", 0, 100_000),  # by M-Pesa ref
        (TODAY, f"PAYBILL {chair.member_code}", 0, 30_000),  # no ref: amount, day, code
    ], 500_000, "b.csv")
    dups = BankTransaction.objects.filter(chama=c, source="statement", state="duplicate")
    assert {d.duplicate_of.external_ref for d in dups} == {"RKTQDM7W6S", "RKTQDM7W6W"}
    contributions = LedgerEntry.objects.filter(chama=c, type="contribution", member=chair)
    assert sum(e.amount_cents for e in contributions) == 130_000  # counted once
    assert daily.summary(c)["gap_cents"] == 500_000  # still only the old deposit


def test_registering_with_safaricom(monkeypatch, settings, setup):
    c, channel, *_ = setup
    settings.DARAJA_CALLBACK_BASE_URL = "https://api.example.ke"
    calls = {}

    class R:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self.body

    monkeypatch.setattr(daraja.requests, "get",
                        lambda url, **kw: R({"access_token": "tok"}))

    def post(url, json, headers, timeout):
        calls.update(url=url, json=json, headers=headers)
        return R({"ResponseDescription": "Success"})

    monkeypatch.setattr(daraja.requests, "post", post)
    assert daraja.register_urls(channel)["ResponseDescription"] == "Success"
    assert calls["json"]["ShortCode"] == "600638"
    assert calls["json"]["ConfirmationURL"] == (
        f"https://api.example.ke/api/v1/hooks/daraja/{channel.token}/confirmation/")
    assert calls["headers"]["Authorization"] == "Bearer tok"
    channel.refresh_from_db()
    assert channel.registered_at is not None
