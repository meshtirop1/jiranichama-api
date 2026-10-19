"""Issuing statements as sealed PDFs, and checking them. See docs/flows/member-statements.md."""

import hashlib
import secrets
from datetime import date

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.chamas.models import Chama, Membership
from apps.core.canonical import canonical_bytes
from apps.core.problems import Problem
from apps.ledger import hashing
from apps.ledger.services import append, document_for

from . import render as pdf
from . import statements
from .labels import LANGUAGES
from .models import DocumentAccess, IssuedDocument

# Crockford Base32: no I, L, O or U, so a code read out over the phone is not misheard.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 10


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def normalise_code(raw: str) -> str:
    code = "".join(ch for ch in (raw or "").upper() if ch.isalnum())
    return code.translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))


def verify_url(code: str) -> str:
    return f"{settings.VERIFY_BASE_URL.rstrip('/')}/{code}/"


def fingerprint(data: dict) -> str:
    return hashlib.sha256(canonical_bytes(data)).hexdigest()


def dates(raw_from: str | None, raw_to: str | None, default_from: date) -> tuple[date, date]:
    today = timezone.localdate()
    try:
        start = date.fromisoformat(raw_from) if raw_from else default_from
        end = date.fromisoformat(raw_to) if raw_to else today
    except ValueError:
        raise Problem("bad_dates", "Dates must look like 2026-10-31.",
                      status.HTTP_400_BAD_REQUEST) from None
    if start > end or end > today:
        raise Problem("bad_dates", "The period must start before it ends, and not end "
                                   "after today.", status.HTTP_400_BAD_REQUEST)
    return start, end


def log(action: str, *, chama: Chama, user=None, subject: Membership | None = None,
        document: IssuedDocument | None = None) -> None:
    DocumentAccess.objects.create(chama=chama, user=user, subject=subject, document=document,
                                  action=action)


def may_issue_member_statement(viewer: Membership, subject: Membership) -> bool:
    """Every member may see every member's figures in the app (records are open). A PDF
    with a name on it, which can be shown to a bank or a lender, is only for the member
    themself and the officials."""
    return viewer.id == subject.id or viewer.is_official or viewer.role == "auditor"


def issue(*, kind: str, chama: Chama, by_user, subject: Membership | None,
          start: date, end: date, language: str) -> IssuedDocument:
    """Make the PDF, or hand back the one already made from exactly these figures."""
    if language not in LANGUAGES:
        raise Problem("bad_language", "Language must be en or sw.",
                      status.HTTP_400_BAD_REQUEST)
    if kind == IssuedDocument.Kind.MEMBER_STATEMENT:
        data = statements.member_statement(subject, start, end)
    else:
        data = statements.chama_statement(chama, start, end)
    fp = fingerprint(data)

    with transaction.atomic():
        existing = (IssuedDocument.objects.filter(chama=chama, kind=kind, membership=subject,
                                                  fingerprint=fp, language=language)
                    .order_by("issued_at").first())
        if existing is not None:
            log(DocumentAccess.Action.ISSUE, chama=chama, user=by_user, subject=subject,
                document=existing)
            return existing

        code = new_code()
        while IssuedDocument.objects.filter(verify_code=code).exists():
            code = new_code()
        body = pdf.render(data, language=language, fingerprint=fp, verify_url=verify_url(code),
                          code=code, issued_on=timezone.localdate())
        sha = hashlib.sha256(body).hexdigest()
        doc = IssuedDocument(chama=chama, membership=subject, kind=kind, period_from=start,
                             period_to=end, language=language, data=data, fingerprint=fp,
                             sha256=sha, verify_code=code, issued_to=by_user)
        doc.seal_entry = append(
            chama=chama, type="seal", source="document", source_id=doc.id, member=subject,
            decision={"document": kind, "verify_code": code, "fingerprint": fp,
                      "pdf_sha256": sha, "from": start.isoformat(), "to": end.isoformat(),
                      "records_to_seq": data["records_to_seq"], "language": language})
        doc.file.save(f"{chama.id}-{code}.pdf", ContentFile(body), save=False)
        doc.save(force_insert=True)
        log(DocumentAccess.Action.ISSUE, chama=chama, user=by_user, subject=subject,
            document=doc)
    return doc


def find(code: str) -> IssuedDocument | None:
    return (IssuedDocument.objects.select_related("chama", "membership", "seal_entry")
            .filter(verify_code=normalise_code(code)).first())


def check(doc: IssuedDocument, file_bytes: bytes | None = None) -> dict:
    """What anyone holding a statement may learn by its code: enough to tell a genuine
    statement from an edited one, and nothing the statement does not already show."""
    seal = doc.seal_entry
    out = {
        "kind": doc.kind,
        "chama_name": doc.chama.name,
        "member_code": doc.membership.member_code if doc.membership_id else None,
        "period_from": doc.period_from.isoformat(),
        "period_to": doc.period_to.isoformat(),
        "issued_at": doc.issued_at.isoformat(),
        "language": doc.language,
        "fingerprint": doc.fingerprint,
        "pdf_sha256": doc.sha256,
        # The seal entry is in the chama's hash chain: it must still hash to itself and
        # name exactly this file and these figures.
        "sealed": hashing.entry_hash(document_for(seal)) == seal.hash
        and seal.decision.get("pdf_sha256") == doc.sha256
        and seal.decision.get("fingerprint") == doc.fingerprint,
        "seal_entry_seq": seal.seq,
        "seal_receipt": seal.receipt_code,
    }
    if file_bytes is not None:
        out["file_matches"] = hashlib.sha256(file_bytes).hexdigest() == doc.sha256
    return out
