"""Member and chama statements: the figures, sealed PDFs, and the public check."""

import hashlib
from datetime import date

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.documents import labels, services, statements
from apps.documents.models import DocumentAccess, IssuedDocument
from apps.fines.services import raise_late_fines
from apps.ledger.models import LedgerEntry
from apps.ledger.services import verify_chain
from apps.reconciliation import services as recon

from .phase1 import active_chama, credit, idem

pytestmark = pytest.mark.django_db

START = date(2026, 1, 1)


def _chama_with_money():
    chama, ms = active_chama(since=START, members=4)
    chair, treasurer, secretary, member = ms
    raise_late_fines(chama, date(2026, 2, 10))  # January late for everyone
    txn = credit(chama, amount_cents=120_000, value_date=date(2026, 2, 3),
                 narration=f"MPESA {member.member_code}")
    recon.match_credit(txn)
    return chama, chair, treasurer, member


def test_member_statement_lists_the_members_money_and_nothing_else():
    chama, _, _, member = _chama_with_money()
    data = statements.member_statement(member, START, timezone.localdate())
    types = [line["type"] for line in data["lines"]]
    assert "membership" not in types and "seal" not in types
    assert types.count("fine_raised") == 2  # January and February
    assert data["totals"]["contributed_cents"] == 100_000
    assert data["totals"]["fines_raised_cents"] == 40_000
    assert types == ["fine_raised", "contribution", "fine_paid", "fine_raised"]  # by date
    assert data["totals"]["fines_paid_cents"] == 20_000
    contribution = next(line for line in data["lines"] if line["type"] == "contribution")
    assert contribution["date"] == "2026-02-03"  # the bank's date, not the day we wrote it
    assert data["member"]["code"] == member.member_code


def test_a_period_only_counts_what_moved_inside_it():
    chama, _, _, member = _chama_with_money()
    data = statements.member_statement(member, date(2026, 3, 1), timezone.localdate())
    assert data["lines"] == []
    assert data["totals"]["contributed_cents"] == 0
    assert data["position"]["contributed_cents"] == 100_000  # position is always today's


def test_pdf_is_sealed_in_the_chain_and_reissued_only_when_figures_change(api):
    chama, _, _, member = _chama_with_money()
    api.force_authenticate(member.user)
    r = api.post(f"/api/v1/memberships/{member.id}/statement/pdf/", {"language": "en"},
                 format="json", **idem())
    assert r.status_code == 201, r.content
    doc = IssuedDocument.objects.get(pk=r.json()["id"])
    body = doc.file.read()
    assert body.startswith(b"%PDF")
    assert hashlib.sha256(body).hexdigest() == doc.sha256 == r.json()["sha256"]

    seal = doc.seal_entry
    assert seal.type == "seal" and seal.member_id == member.id
    assert seal.decision["pdf_sha256"] == doc.sha256
    assert seal.decision["fingerprint"] == doc.fingerprint
    assert verify_chain(chama).ok

    # The same figures again: the same document, no second seal.
    again = api.post(f"/api/v1/memberships/{member.id}/statement/pdf/", {"language": "en"},
                     format="json", **idem())
    assert again.json()["id"] == str(doc.id)
    assert LedgerEntry.objects.filter(chama=chama, type="seal").count() == 1

    # New money in: new figures, new document.
    recon.match_credit(credit(chama, amount_cents=100_000, value_date=date(2026, 3, 2),
                              narration=f"MPESA {member.member_code}"))
    third = api.post(f"/api/v1/memberships/{member.id}/statement/pdf/", {"language": "en"},
                     format="json", **idem())
    assert third.json()["id"] != str(doc.id)
    assert third.json()["fingerprint"] != doc.fingerprint


def test_kiswahili_pdf_has_the_same_figures_and_its_own_document(api):
    chama, _, _, member = _chama_with_money()
    api.force_authenticate(member.user)
    en = api.post(f"/api/v1/memberships/{member.id}/statement/pdf/", {"language": "en"},
                  format="json", **idem()).json()
    sw = api.post(f"/api/v1/memberships/{member.id}/statement/pdf/", {"language": "sw"},
                  format="json", **idem()).json()
    assert sw["fingerprint"] == en["fingerprint"]
    assert sw["id"] != en["id"] and sw["sha256"] != en["sha256"]


def test_labels_exist_in_both_languages():
    def keys(d, prefix=""):
        out = set()
        for k, v in d.items():
            out |= keys(v, f"{prefix}{k}.") if isinstance(v, dict) else {prefix + k}
        return out
    assert keys(labels.LABELS["en"]) == keys(labels.LABELS["sw"])
    assert set(labels.LABELS["en"]["types"]) == set(statements.MEMBER_TYPES)


def test_any_member_sees_the_figures_but_only_the_member_or_officials_get_the_pdf(api):
    chama, chair, treasurer, member = _chama_with_money()
    api.force_authenticate(member.user)
    assert api.get(f"/api/v1/memberships/{treasurer.id}/statement/").status_code == 200
    r = api.post(f"/api/v1/memberships/{treasurer.id}/statement/pdf/", {}, format="json",
                 **idem())
    assert r.status_code == 403

    api.force_authenticate(chair.user)
    r = api.post(f"/api/v1/memberships/{member.id}/statement/pdf/", {}, format="json",
                 **idem())
    assert r.status_code == 201
    doc_id = r.json()["id"]

    api.force_authenticate(treasurer.user)
    assert api.get(f"/api/v1/documents/{doc_id}/file/").status_code == 200
    other = active_chama(since=START)[1][0]
    api.force_authenticate(other.user)
    assert api.get(f"/api/v1/documents/{doc_id}/file/").status_code == 404


def test_download_and_every_look_are_logged(api):
    chama, _, _, member = _chama_with_money()
    api.force_authenticate(member.user)
    api.get(f"/api/v1/memberships/{member.id}/statement/")
    doc_id = api.post(f"/api/v1/memberships/{member.id}/statement/pdf/", {}, format="json",
                      **idem()).json()["id"]
    r = api.get(f"/api/v1/documents/{doc_id}/file/")
    assert r["Content-Type"] == "application/pdf"
    assert "attachment" in r["Content-Disposition"]
    actions = list(DocumentAccess.objects.filter(subject=member).order_by("id")
                   .values_list("action", flat=True))
    assert actions == ["view", "issue", "download"]


def test_anyone_can_check_a_statement_by_its_code_without_signing_in(api):
    chama, _, _, member = _chama_with_money()
    doc = services.issue(kind="member_statement", chama=chama, by_user=member.user,
                         subject=member, start=START, end=timezone.localdate(),
                         language="en")
    api.force_authenticate(None)
    spoken = f"{doc.verify_code[:5].lower()}-{doc.verify_code[5:]}"
    r = api.get(f"/api/v1/verify/{spoken}/")
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["sealed"] is True and body["member_code"] == member.member_code
    assert "name" not in body  # the check page does not give out names

    good = api.post(f"/api/v1/verify/{doc.verify_code}/",
                    {"file": _upload(doc.file.read())}, format="multipart")
    assert good.json()["file_matches"] is True
    edited = api.post(f"/api/v1/verify/{doc.verify_code}/",
                      {"file": _upload(b"%PDF-1.4 edited")}, format="multipart")
    assert edited.json()["file_matches"] is False

    page = api.get(f"/verify/{doc.verify_code}/")
    assert page.status_code == 200 and b"Genuine" in page.content
    assert api.get("/verify/ZZZZZZZZZZ/").status_code == 404
    assert api.get("/api/v1/verify/ZZZZZZZZZZ/").status_code == 404
    assert DocumentAccess.objects.filter(document=doc, action="verify", user=None).count() == 4


def _upload(data: bytes):
    from django.core.files.uploadedfile import SimpleUploadedFile
    return SimpleUploadedFile("statement.pdf", data, content_type="application/pdf")


def test_an_issued_statement_cannot_be_edited_in_the_database():
    chama, _, _, member = _chama_with_money()
    doc = services.issue(kind="member_statement", chama=chama, by_user=member.user,
                         subject=member, start=START, end=timezone.localdate(),
                         language="en")
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as c:
        c.execute("UPDATE documents_issued SET fingerprint = %s WHERE id = %s",
                  ["0" * 64, doc.id])


def test_chama_statement_totals_every_member_and_the_bank(api):
    chama, chair, _, member = _chama_with_money()
    api.force_authenticate(member.user)
    data = api.get(f"/api/v1/chamas/{chama.id}/statement/").json()
    assert len(data["members"]) == 4
    assert data["totals"]["savings_cents"] == sum(m["savings_cents"] for m in data["members"])
    assert data["totals"]["contributed_cents"] == 100_000
    assert data["bank"]["reconciled_to"] is None  # credits in tests came without a statement
    r = api.post(f"/api/v1/chamas/{chama.id}/statement/pdf/", {"language": "sw"},
                 format="json", **idem())
    assert r.status_code == 201
    assert r.json()["member_code"] is None
    assert LedgerEntry.objects.get(type="seal", chama=chama).member_id is None


@pytest.mark.parametrize("query", ["?to=2999-01-01", "?from=2026-05-01&to=2026-04-01",
                                   "?from=yesterday"])
def test_bad_periods_are_refused(api, query):
    chama, _, _, member = _chama_with_money()
    api.force_authenticate(member.user)
    r = api.get(f"/api/v1/memberships/{member.id}/statement/{query}")
    assert r.status_code == 400 and r.json()["code"] == "bad_dates"


def test_default_period_starts_when_the_member_joined():
    chama, _, _, member = _chama_with_money()
    assert statements.default_start(chama, member) == START
