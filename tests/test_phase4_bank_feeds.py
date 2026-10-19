"""Bank feeds through the bank's API: Equity Jenga (Phase 4)."""

import base64
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.utils import timezone

from apps.chamas.services import active_bank_account
from apps.feeds import bank_feed
from apps.feeds.jenga import Client
from apps.feeds.models import BankFeed, BankTransaction
from apps.ledger.models import LedgerEntry
from apps.reconciliation.daily import reconcile

from .phase1 import active_chama, statement_csv

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption()).decode()
CREDS = {"api_key": "k", "merchant_code": "m", "consumer_secret": "s", "private_key_pem": PEM}


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class FakeJenga:
    """Answers like Jenga v3, and checks every request is signed by the account key."""

    def __init__(self, lines, balance="2500.00", fail=False):
        self.lines, self.balance, self.fail = lines, balance, fail
        self.calls = []

    def _check(self, headers, message):
        sig = base64.b64decode(headers["signature"])
        KEY.public_key().verify(sig, message.encode(), padding.PKCS1v15(), hashes.SHA256())

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(url)
        if self.fail:
            return _Resp(503, {})
        if url.endswith("/authenticate/merchant"):
            assert headers["Api-Key"] == "k" and json["merchantCode"] == "m"
            return _Resp(200, {"accessToken": "tok"})
        self._check(headers, f"{json['accountNumber']}{json['countryCode']}{json['toDate']}")
        return _Resp(200, {"status": True, "data": {"transactions": self.lines}})

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        country, account = url.rstrip("/").split("/")[-2:]
        self._check(headers, f"{country}{account}")
        return _Resp(200, {"data": {"balances": [{"type": "Available", "amount": "1.00"},
                                                 {"type": "Current", "amount": self.balance}]}})


def _feed(c):
    feed = BankFeed(chama=c, bank_account=active_bank_account(c), account_number="0123456789",
                    start_date=TODAY - timedelta(days=30))
    feed.set_credentials(CREDS)
    feed.save()
    return feed


def _line(amount, kind, member_code="", ref="REF1", days_ago=1):
    return {"date": (TODAY - timedelta(days=days_ago)).isoformat(), "amount": amount,
            "type": kind, "description": f"DEPOSIT {member_code}".strip(),
            "reference": ref, "runningBalance": "2500.00"}


def test_the_client_signs_and_reads_balance_and_lines():
    jenga = FakeJenga([_line("1,000.00", "Credit", "JC-1-001")])
    client = Client(CREDS, base_url="https://uat.example", session=jenga)
    assert client.balance_cents("KE", "0123") == 250_000
    [line] = client.statement("KE", "0123", TODAY - timedelta(days=5), TODAY)
    assert line["direction"] == "credit" and line["amount_cents"] == 100_000
    assert line["reference"] == "REF1"


def test_a_pull_matches_payments_and_reconciles_from_the_feed():
    c, (chair, treasurer, member) = active_chama(since=TODAY - timedelta(days=40))
    feed = _feed(c)
    jenga = FakeJenga([_line("1000.00", "Credit", treasurer.member_code)], balance="1000.00")
    p = bank_feed.pull(feed, client=Client(CREDS, base_url="https://x", session=jenga))
    assert p.new_lines == 1 and not p.error
    txn = BankTransaction.objects.get(source="bank_api")
    assert txn.state == "recorded"
    assert LedgerEntry.objects.filter(type="contribution", member=treasurer).exists()
    feed.refresh_from_db()
    assert feed.opening_cents == 0 and feed.last_balance_cents == 100_000
    run = reconcile(c)
    assert run.source == "bank_api" and run.gap_cents == 0

    # The same pull again changes nothing.
    p = bank_feed.pull(feed, client=Client(CREDS, base_url="https://x", session=jenga))
    assert p.new_lines == 0
    assert LedgerEntry.objects.filter(type="contribution", member=treasurer).count() == 1


def test_a_statement_line_already_read_from_the_feed_is_a_duplicate(settings):
    from apps.feeds import services as feeds

    settings.BANK_STATEMENT_SENDERS = {"equity": ["equitybank.example"]}
    c, (chair, treasurer, member) = active_chama(since=TODAY - timedelta(days=40))
    feed = _feed(c)
    jenga = FakeJenga([_line("1000.00", "Credit", treasurer.member_code)])
    bank_feed.pull(feed, client=Client(CREDS, base_url="https://x", session=jenga))
    first = BankTransaction.objects.get(source="bank_api")

    data = statement_csv([(TODAY - timedelta(days=1), f"DEPOSIT {treasurer.member_code}",
                           0, 100_000)], opening_cents=0)
    [st] = feeds.receive_mail({
        "to": c.statement_inbox.address, "from": "statements@equitybank.example",
        "auth": {"spf": "pass", "dkim": "pass", "dmarc": "pass",
                 "dkim_domain": "equitybank.example"},
        "attachments": [{"filename": "s.csv", "content_b64": base64.b64encode(data).decode()}],
    })
    assert st.state == "accepted", st.problems
    txn = BankTransaction.objects.get(source="statement")
    assert txn.state == "duplicate" and txn.duplicate_of_id == first.id
    assert LedgerEntry.objects.filter(type="contribution", member=treasurer).count() == 1


def test_a_failed_pull_is_recorded_and_changes_nothing():
    c, _ = active_chama(since=TODAY)
    feed = _feed(c)
    p = bank_feed.pull(feed, client=Client(CREDS, base_url="https://x",
                                           session=FakeJenga([], fail=True)))
    assert "HTTP 503" in p.error
    feed.refresh_from_db()
    assert feed.last_error and feed.last_balance_cents is None
    assert BankTransaction.objects.count() == 0


def test_credentials_are_stored_encrypted():
    c, _ = active_chama(since=TODAY)
    feed = _feed(c)
    raw = bytes(BankFeed.objects.get(pk=feed.pk).credentials_enc)
    assert b"consumer_secret" not in raw and PEM.encode()[:30] not in raw
    assert feed.credentials()["merchant_code"] == "m"
