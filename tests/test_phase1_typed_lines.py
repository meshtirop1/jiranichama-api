"""Statement lines the parser could not read, typed in by an official (5.5)."""

import base64
import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.core.problems import Problem
from apps.feeds import services as feeds
from apps.feeds.models import BankTransaction, Statement, TypedLine
from apps.ledger.models import LedgerEntry
from apps.ledger.services import verify_chain

from .phase1 import active_chama, idem, make_user, statement_csv

pytestmark = pytest.mark.django_db


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


def _csv_with_unreadable_line(treasurer_code: str) -> bytes:
    today = timezone.localdate()
    data = statement_csv([
        (today, f"MPESA C2B {treasurer_code} PETER", 0, 100_000),
        (today, "CASH DEPOSIT", 0, 30_000),
        (today, "LEDGER FEE", 1_500, 0),
    ], opening_cents=500_000)
    # Damage the second line's date the way a bank export sometimes does.
    d = today.strftime("%d/%m/%Y").encode()
    return data.replace(d + b",CASH DEPOSIT", b"##/##/####,CASH DEPOSIT")


def _upload(api, chama, user, data):
    api.force_authenticate(user)
    return api.post(f"/api/v1/chamas/{chama.id}/statements/",
                    {"file": SimpleUploadedFile("march.csv", data, content_type="text/csv")},
                    format="multipart", **idem())


def _typed(line_no, credit=30_000, debit=0, day=None, balance=None):
    return {"line_no": line_no, "value_date": (day or timezone.localdate()).isoformat(),
            "narration": "CASH DEPOSIT", "credit_cents": credit, "debit_cents": debit,
            "balance_cents": balance}


def _type_in(api, statement_id, user, lines):
    api.force_authenticate(user)
    return api.post(f"/api/v1/statements/{statement_id}/typed-lines/", {"lines": lines},
                    format="json", **idem())


@pytest.fixture
def setup():
    chama, (chair, treasurer, secretary) = active_chama(since=timezone.localdate())
    return chama, chair, treasurer, secretary


def test_a_statement_with_an_unreadable_line_waits_for_typing(api, setup):
    chama, _, treasurer, secretary = setup
    r = _upload(api, chama, secretary.user, _csv_with_unreadable_line(treasurer.member_code))
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["state"] == "needs_typing"
    [row] = body["unreadable_lines"]
    assert row["cells"][1] == "CASH DEPOSIT" and row["cells"][0] == "##/##/####"
    assert body["lines"] == 2
    assert not BankTransaction.objects.filter(chama=chama).exists()  # nothing counted yet


def test_typed_lines_must_make_the_statement_add_up(api, setup):
    chama, _, treasurer, secretary = setup
    sid = _upload(api, chama, secretary.user,
                  _csv_with_unreadable_line(treasurer.member_code)).json()["id"]
    line_no = Statement.objects.get(pk=sid).unreadable_lines[0]["line_no"]

    r = _type_in(api, sid, treasurer.user, [_typed(line_no, credit=3_000)])  # a slip
    assert r.status_code == 400
    assert r.json()["code"] == "typed_lines_do_not_add_up"
    assert "balances_do_not_add_up" in [p["code"] for p in r.json()["problems"]]
    assert not TypedLine.objects.exists()
    assert Statement.objects.get(pk=sid).state == "needs_typing"

    r = _type_in(api, sid, treasurer.user, [])
    assert r.json()["code"] == "typed_lines_incomplete"
    r = _type_in(api, sid, treasurer.user, [_typed(line_no, credit=30_000, debit=5)])
    assert r.json()["code"] == "typed_line_invalid"


def test_typed_line_is_confirmed_by_someone_else_and_marked_on_the_records(
        api, setup, sms_outbox):
    chama, chair, treasurer, secretary = setup
    sid = _upload(api, chama, secretary.user,
                  _csv_with_unreadable_line(treasurer.member_code)).json()["id"]
    statement = Statement.objects.get(pk=sid)
    line_no = statement.unreadable_lines[0]["line_no"]

    r = _type_in(api, sid, treasurer.user, [_typed(line_no)])
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["state"] == "awaiting_confirmation"
    assert body["lines"] == 3 and body["credits_cents"] == 130_000
    assert body["typed_lines"][0]["typed_by"] == "Treasurer"

    statement.refresh_from_db()
    for who in (treasurer, secretary):  # the typist and the uploader cannot confirm
        with pytest.raises(Problem) as exc:
            feeds.start_confirm(statement, who)
        assert exc.value.problem_code == "second_official_required"
    ch = feeds.start_confirm(statement, chair)
    statement = feeds.confirm(statement, chair, challenge_id=ch.id, sms_code=_code(sms_outbox))
    assert statement.state == "accepted"

    typed_txn = BankTransaction.objects.get(chama=chama, amount_cents=30_000)
    assert typed_txn.typed_by_id == treasurer.user_id
    read_txn = BankTransaction.objects.get(chama=chama, amount_cents=100_000)
    assert read_txn.typed_by_id is None
    assert LedgerEntry.objects.get(source_id=read_txn.id, type="contribution") \
        .decision.get("typed_in") is None

    api.force_authenticate(chair.user)
    unmatched = api.get(f"/api/v1/chamas/{chama.id}/unmatched/").json()
    assert [(u["amount_cents"], u["typed_in"]) for u in unmatched] == [(30_000, True)]
    assert verify_chain(chama, check_signatures=False).ok


def test_a_typed_line_that_is_a_members_payment_says_so_on_the_chain(api, setup, sms_outbox):
    chama, chair, treasurer, secretary = setup
    data = _csv_with_unreadable_line(treasurer.member_code)
    sid = _upload(api, chama, secretary.user, data).json()["id"]
    line_no = Statement.objects.get(pk=sid).unreadable_lines[0]["line_no"]
    line = {**_typed(line_no), "narration": f"DEPOSIT {secretary.member_code}"}
    assert _type_in(api, sid, chair.user, [line]).status_code == 200
    statement = Statement.objects.get(pk=sid)
    ch = feeds.start_confirm(statement, treasurer)
    feeds.confirm(statement, treasurer, challenge_id=ch.id, sms_code=_code(sms_outbox))
    entry = LedgerEntry.objects.get(member=secretary, type="contribution")
    assert entry.decision["typed_in"] is True


def test_only_officials_type_lines_in(api, setup):
    chama, _, treasurer, secretary = setup
    sid = _upload(api, chama, secretary.user,
                  _csv_with_unreadable_line(treasurer.member_code)).json()["id"]
    member = make_user("+254799000111", "Member")
    from apps.chamas.models import Membership
    Membership.objects.create(chama=chama, user=member,
                              member_code=f"{chama.member_code_prefix}-099")
    line_no = Statement.objects.get(pk=sid).unreadable_lines[0]["line_no"]
    assert _type_in(api, sid, member, [_typed(line_no)]).status_code == 403


def test_a_bank_emailed_statement_with_typed_lines_needs_a_second_official(
        settings, setup):
    settings.BANK_STATEMENT_SENDERS = {"equity": ["equitybank.example"]}
    chama, chair, treasurer, _ = setup
    data = _csv_with_unreadable_line(treasurer.member_code)
    [statement] = feeds.receive_mail({
        "to": chama.statement_inbox.address, "from": "statements@equitybank.example",
        "auth": {"spf": "pass", "dkim": "pass", "dmarc": "pass",
                 "dkim_domain": "equitybank.example"},
        "attachments": [{"filename": "s.csv", "content_b64": base64.b64encode(data).decode()}],
    })
    assert statement.state == "needs_typing"
    line_no = statement.unreadable_lines[0]["line_no"]
    statement = feeds.submit_typed_lines(statement, treasurer, [_typed(line_no)])
    assert statement.state == "awaiting_confirmation"  # not accepted on its own


def test_typed_lines_cannot_be_edited_in_the_database(api, setup):
    chama, _, treasurer, secretary = setup
    sid = _upload(api, chama, secretary.user,
                  _csv_with_unreadable_line(treasurer.member_code)).json()["id"]
    line_no = Statement.objects.get(pk=sid).unreadable_lines[0]["line_no"]
    _type_in(api, sid, treasurer.user, [_typed(line_no)])
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as c:
        c.execute("UPDATE feeds_typed_line SET credit_cents = 1")
