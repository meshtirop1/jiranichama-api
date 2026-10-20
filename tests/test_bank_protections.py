"""Bank protections (2026-10-20): the bank mandate, statements sealed on the chain, no
skipped periods, uploads that must agree with the bank's own feed, and a harder M-Pesa
callback."""

import base64
import re
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.chamas import mandate
from apps.chamas.models import BankMandate
from apps.core.problems import Problem
from apps.feeds import services as feeds
from apps.feeds.models import BankTransaction, DarajaChannel
from apps.ledger.models import LedgerEntry
from apps.payouts import services as payouts

from .phase1 import active_chama, statement_csv

pytestmark = pytest.mark.django_db
TODAY = timezone.localdate()


@pytest.fixture(autouse=True)
def mail(settings):
    settings.BANK_STATEMENT_SENDERS = {"equity": ["equitybank.example"]}


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


def test_the_mandate_needs_two_signers_and_a_second_official(api, sms_outbox):
    c, (chair, treasurer, secretary, mary, john) = active_chama(since=TODAY, members=5,
                                                                mandate=False)
    api.force_authenticate(chair.user)
    url = f"/api/v1/chamas/{c.id}/bank-mandate/"

    def post(ids, n):
        return api.post(url, {"file": SimpleUploadedFile("m.pdf", b"%PDF mandate"),
                              "signatory_ids": [str(i) for i in ids], "min_signers": n,
                              "bank_app_approvals": "true"},
                        format="multipart")

    r = post([chair.id], 1)
    assert r.status_code == 400 and r.json()["code"] == "too_few_signatories"
    r = post([chair.id, treasurer.id], 1)
    assert r.json()["code"] == "one_person_can_sign"
    r = post([chair.id, treasurer.id, secretary.id], 2)
    assert r.status_code == 201
    pending = r.json()["pending"]
    assert pending["min_signers"] == 2 and r.json()["current"] is None

    # The official who uploaded it cannot confirm it.
    r = api.post(f"/api/v1/bank-mandates/{pending['id']}/confirm/", {}, format="json")
    assert r.status_code == 403
    api.force_authenticate(treasurer.user)
    r = api.post(f"/api/v1/bank-mandates/{pending['id']}/confirm/", {}, format="json")
    assert r.status_code == 202 and "CONFIRM the bank mandate" in sms_outbox[-1][1]
    r = api.post(f"/api/v1/bank-mandates/{pending['id']}/confirm/",
                 {"challenge_id": r.json()["challenge_id"], "code": _code(sms_outbox)},
                 format="json")
    assert r.status_code == 200 and r.json()["safe"]
    cur = r.json()["current"]
    assert cur["confirmed_by"] == treasurer.member_code and cur["receipt"]
    e = LedgerEntry.objects.get(chama=c, type="bank_mandate")
    assert e.decision["min_signers"] == 2 and len(e.decision["signatories"]) == 3
    assert api.get(f"/api/v1/bank-mandates/{cur['id']}/file/").status_code == 200


def test_money_out_cannot_be_switched_on_without_a_safe_mandate():
    c, (chair, treasurer, secretary) = active_chama(since=TODAY, mandate=False)
    with pytest.raises(Problem) as exc:
        payouts.start_enable(c, chair)
    assert exc.value.problem_code == "mandate_missing"


def test_a_new_confirmed_mandate_replaces_the_old_one(sms_outbox):
    c, (chair, treasurer, secretary) = active_chama(since=TODAY)
    old = mandate.current(c)
    m = mandate.propose(c, secretary, signatory_ids=[chair.id, secretary.id], min_signers=2,
                        bank_app_approvals=False, filename="m.jpg", data=b"\xff\xd8 m")
    ch = mandate.start_confirm(m, chair)
    mandate.confirm(m, chair, challenge_id=ch.id, sms_code=_code(sms_outbox))
    old.refresh_from_db()
    assert old.state == BankMandate.State.REPLACED and mandate.current(c).id == m.id


def _mail(c, data, name="s.csv"):
    return feeds.receive_mail({
        "to": c.statement_inbox.address, "from": "s@equitybank.example",
        "auth": {"spf": "pass", "dkim": "pass", "dmarc": "pass",
                 "dkim_domain": "equitybank.example"},
        "attachments": [{"filename": name, "content_b64": base64.b64encode(data).decode()}]})


def test_an_accepted_statement_is_sealed_on_the_chain():
    c, _ = active_chama(since=TODAY - timedelta(days=20))
    data = statement_csv([(TODAY - timedelta(days=2), "DEPOSIT", 0, 50_000)],
                         opening_cents=0)
    [s] = _mail(c, data)
    assert s.state == "accepted"
    e = LedgerEntry.objects.get(chama=c, type="statement_accepted")
    assert e.decision["file_sha256"] == s.sha256 and e.decision["lines"] == 1
    assert e.decision["closing_cents"] == 50_000 and len(e.decision["lines_sha256"]) == 64


def test_an_official_cannot_skip_a_period():
    c, (chair, treasurer, secretary) = active_chama(since=TODAY - timedelta(days=40))
    first = statement_csv([(TODAY - timedelta(days=30), "DEPOSIT", 0, 50_000)],
                          opening_cents=0)
    [s1] = _mail(c, first)
    assert s1.state == "accepted"
    later = statement_csv([(TODAY - timedelta(days=5), "DEPOSIT", 0, 10_000)],
                          opening_cents=50_000)
    s2 = feeds.upload(c, secretary, filename="later.csv", data=later)
    assert s2.state == "rejected" and [p["code"] for p in s2.problems] == ["gap"]


def test_an_upload_must_agree_with_the_banks_own_feed():
    c, (chair, treasurer, secretary) = active_chama(since=TODAY - timedelta(days=20))
    account = c.bank_accounts.get(active_to__isnull=True)
    day = TODAY - timedelta(days=3)
    txn = BankTransaction(chama=c, bank_account=account, source=BankTransaction.Source.BANK_API,
                          external_ref="API-1", direction="debit", amount_cents=200_000,
                          value_date=day, state=BankTransaction.State.UNAPPROVED)
    txn.set_narration("ATM WITHDRAWAL")
    txn.save()
    # A doctored statement that leaves out the withdrawal the bank's API reported.
    doctored = statement_csv([(day, "DEPOSIT", 0, 50_000)], opening_cents=0)
    s = feeds.upload(c, secretary, filename="doctored.csv", data=doctored)
    assert s.state == "rejected" and "differs_from_bank_feed" in [p["code"] for p in s.problems]


def _payload(trans_id, amount="500.00", shortcode="600638"):
    return {"TransactionType": "Pay Bill", "TransID": trans_id,
            "TransTime": TODAY.strftime("%Y%m%d") + "093015", "TransAmount": amount,
            "BusinessShortCode": shortcode, "BillRefNumber": "X", "MSISDN": "254799000123",
            "FirstName": "A", "MiddleName": "", "LastName": "B"}


def test_the_mpesa_callback_ignores_what_does_not_fit(api, settings):
    c, _ = active_chama(since=TODAY)
    ch = DarajaChannel.objects.create(chama=c, shortcode="600638")
    url = f"/api/v1/hooks/daraja/{ch.token}/confirmation/"
    api.post(url, _payload("QKL1234ABC", shortcode="999999"), format="json")
    api.post(url, _payload("bad id!"), format="json")
    api.post(url, _payload("QKL1234ABD", amount="0"), format="json")
    api.post(url, _payload("QKL1234ABE", amount="99999999"), format="json")
    assert not BankTransaction.objects.filter(chama=c, source="daraja").exists()
    api.post(url, _payload("QKL1234ABF"), format="json")
    assert BankTransaction.objects.filter(chama=c, source="daraja").count() == 1

    # Behind our proxy, the caller is the address the proxy saw, not one they wrote.
    settings.DARAJA_ALLOWED_IPS = ["196.201.214.200"]
    settings.DARAJA_TRUST_PROXY = True
    r = api.post(url, _payload("QKL1234ABG"), format="json",
                 HTTP_X_FORWARDED_FOR="196.201.214.200, 10.0.0.9")
    assert r.status_code == 404
    r = api.post(url, _payload("QKL1234ABG"), format="json",
                 HTTP_X_FORWARDED_FOR="10.0.0.9, 196.201.214.200")
    assert r.status_code == 200
