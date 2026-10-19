"""Bank statements in, by the bank's own email or an official's upload (5.5).

A statement uploaded by an official is not trusted until a *different* official confirms
it with an SMS code, and the treasurer cannot upload alone while the chama has another
official. A statement emailed by the bank (mail authenticated to an allow-listed bank
domain) is accepted without anyone handling the file.
"""

import base64
import hashlib
import hmac

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status

from apps.chamas.access import officials
from apps.chamas.models import OFFICIAL_ROLES, Chama, Membership, StatementInbox
from apps.chamas.services import active_bank_account
from apps.core.canonical import canonical_bytes
from apps.core.problems import Problem
from apps.devices import signing
from apps.devices.models import SigningChallenge

from . import checks, parsers
from .models import BankTransaction, Statement

CONFIRMERS = OFFICIAL_ROLES + ("auditor",)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build(chama: Chama, *, route: str, filename: str, data: bytes, uploaded_by=None,
           mail_auth: dict | None = None, forced_state: str | None = None) -> Statement:
    account = active_bank_account(chama)
    if account is None:
        raise Problem("bank_account_missing", "Add the chama's bank account first.",
                      status.HTTP_409_CONFLICT)
    digest = _sha(data)
    if Statement.objects.filter(chama=chama, sha256=digest).exists():
        raise Problem("statement_duplicate", "This exact file was uploaded before.",
                      status.HTTP_409_CONFLICT)

    fields = dict(chama=chama, bank_account=account, route=route, uploaded_by=uploaded_by,
                  filename=filename[:200], sha256=digest, mail_auth=mail_auth or {})
    try:
        parsed = parsers.parse(account.bank, filename, data)
    except parsers.NeedsParser as exc:
        fields.update(state=Statement.State.NEEDS_PARSER,
                      problems=[{"code": "needs_parser", "message": str(exc)}])
        parsed = None
    except parsers.StatementUnreadable as exc:
        fields.update(state=Statement.State.REJECTED,
                      problems=[{"code": "unreadable", "message": str(exc),
                                 "lines": exc.lines}])
        parsed = None

    if parsed is not None:
        problems, flags = checks.check(parsed, account, timezone.localdate())
        fields.update(
            parser=parsed.parser, period_from=parsed.period_from, period_to=parsed.period_to,
            opening_cents=parsed.opening_cents, closing_cents=parsed.closing_cents,
            parsed_lines=[line.as_dict() for line in parsed.lines], problems=problems,
            flags=flags,
            state=(Statement.State.REJECTED if problems
                   else Statement.State.ACCEPTED if route == Statement.Route.BANK_EMAIL
                   else Statement.State.AWAITING_CONFIRMATION),
        )
    if forced_state:
        fields["state"] = forced_state
    statement = Statement(**fields)
    statement.file.save(f"{chama.id}-{digest[:16]}-{filename[-60:]}", ContentFile(data),
                        save=False)
    try:
        with transaction.atomic():
            statement.save()
    except IntegrityError as exc:
        raise Problem("statement_duplicate", "This exact file was uploaded before.",
                      status.HTTP_409_CONFLICT) from exc
    if statement.state == Statement.State.ACCEPTED:
        ingest(statement)
    return statement


def upload(chama: Chama, by: Membership, *, filename: str, data: bytes) -> Statement:
    if chama.state != Chama.State.ACTIVE:
        raise Problem("chama_not_active", "Statements are uploaded once the chama is active.",
                      status.HTTP_409_CONFLICT)
    if by.role not in CONFIRMERS:
        raise Problem("not_allowed", "Only an official uploads statements.",
                      status.HTTP_403_FORBIDDEN)
    others = officials(chama).exclude(pk=by.pk).exists()
    if by.role == Membership.Role.TREASURER and others:
        # The person the statement checks does not hold the evidence (v1.1, P.2).
        raise Problem("treasurer_cannot_upload",
                      "Another official uploads the statement, so the treasurer is not "
                      "checking their own work.", status.HTTP_403_FORBIDDEN)
    if len(data) > settings.STATEMENT_MAX_BYTES:
        raise Problem("file_too_large", "The file is too large.",
                      status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
    return _build(chama, route=Statement.Route.UPLOAD, filename=filename, data=data,
                  uploaded_by=by.user)


def _confirm_hash(statement: Statement) -> str:
    return hashlib.sha256(canonical_bytes({
        "statement": str(statement.id), "sha256": statement.sha256,
        "closing_cents": statement.closing_cents, "lines": len(statement.parsed_lines),
    })).hexdigest()


def _require_confirmer(statement: Statement, by: Membership) -> None:
    if statement.state != Statement.State.AWAITING_CONFIRMATION:
        raise Problem("not_awaiting_confirmation", "This statement is not waiting for "
                                                   "confirmation.", status.HTTP_409_CONFLICT)
    if by.role not in CONFIRMERS:
        raise Problem("not_allowed", "Only an official confirms statements.",
                      status.HTTP_403_FORBIDDEN)
    if statement.uploaded_by_id == by.user_id:
        raise Problem("second_official_required", "A different official must confirm the "
                                                  "statement you uploaded.",
                      status.HTTP_403_FORBIDDEN)


def start_confirm(statement: Statement, by: Membership) -> SigningChallenge:
    _require_confirmer(statement, by)
    closing = f"KES {statement.closing_cents / 100:,.2f}"
    action = (f"CONFIRM the bank statement {statement.period_from} to {statement.period_to}, "
              f"closing balance {closing}") if by.user.language != "sw" else (
        f"KUTHIBITISHA taarifa ya benki {statement.period_from} hadi {statement.period_to}, "
        f"salio {closing}")
    return signing.issue(user=by.user, purpose=SigningChallenge.Purpose.CONFIRM_STATEMENT,
                         subject=f"feeds.statement:{statement.id}",
                         content_hash=_confirm_hash(statement), action_text=action)


def confirm(statement: Statement, by: Membership, *, challenge_id, sms_code: str
            ) -> Statement:
    _require_confirmer(statement, by)
    signing.confirm(user=by.user, challenge_id=challenge_id, code=sms_code,
                    purpose=SigningChallenge.Purpose.CONFIRM_STATEMENT,
                    subject=f"feeds.statement:{statement.id}",
                    content_hash=_confirm_hash(statement))
    with transaction.atomic():
        statement = Statement.objects.select_for_update().get(pk=statement.pk)
        _require_confirmer(statement, by)
        statement.state = Statement.State.ACCEPTED
        statement.confirmed_by = by.user
        statement.confirmed_at = timezone.now()
        statement.save(update_fields=["state", "confirmed_by", "confirmed_at"])
        ingest(statement)
    return statement


def ingest(statement: Statement) -> list[BankTransaction]:
    """Turn an accepted statement's lines into bank transactions. Lines already seen from
    an earlier statement are skipped (they share the bank's reference). Credits go to
    matching; debits wait for Phase 2's debit matching."""
    from apps.reconciliation.services import match_credit

    created = []
    for raw in statement.parsed_lines:
        line = parsers.ParsedLine.from_dict(raw)
        credit = line.credit_cents > 0
        txn = BankTransaction(
            chama=statement.chama, bank_account=statement.bank_account,
            source=BankTransaction.Source.STATEMENT, external_ref=line.reference[:100],
            direction=BankTransaction.Direction.CREDIT if credit
            else BankTransaction.Direction.DEBIT,
            amount_cents=line.credit_cents if credit else line.debit_cents,
            value_date=line.value_date, balance_cents=line.balance_cents, statement=statement,
            state=BankTransaction.State.UNMATCHED if credit
            else BankTransaction.State.DEBIT_PENDING,
        )
        txn.set_narration(line.narration)
        try:
            with transaction.atomic():
                txn.save()
        except IntegrityError:
            continue  # already have this line from an overlapping statement
        created.append(txn)
    for txn in created:
        if txn.direction == BankTransaction.Direction.CREDIT:
            match_credit(txn)
    return created


# --- Statement inbox --------------------------------------------------------------------

def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].strip().lower().rstrip(">")


def mail_passes(auth: dict, sender: str, bank: str) -> tuple[bool, str]:
    """SPF, DKIM and DMARC must all pass, aligned to a domain on the bank's allowlist.
    The allowlist is configured per bank and is empty until each bank's sending domain is
    confirmed, so by default every mail is held for staff."""
    allowed = {d.lower() for d in settings.BANK_STATEMENT_SENDERS.get(bank, [])}
    if not allowed:
        return False, "sender_not_configured"
    domain = _domain(sender)
    if domain not in allowed:
        return False, "sender_not_allowed"
    for check in ("spf", "dkim", "dmarc"):
        if (auth.get(check) or "").lower() != "pass":
            return False, f"{check}_failed"
    if _domain(auth.get("dkim_domain", "")) not in allowed:
        return False, "dkim_not_aligned"
    return True, "ok"


def receive_mail(payload: dict) -> list[Statement]:
    """Called by the inbound-mail hook with a provider-neutral message: ``to``, ``from``,
    ``auth`` (spf, dkim, dmarc, dkim_domain) and ``attachments`` (filename, content_b64).
    """
    inbox = StatementInbox.objects.select_related("chama").filter(
        address__iexact=(payload.get("to") or "").strip(), active=True).first()
    if inbox is None or inbox.chama.state != Chama.State.ACTIVE:
        return []
    account = active_bank_account(inbox.chama)
    if account is None:
        return []
    ok, why = mail_passes(payload.get("auth") or {}, payload.get("from") or "", account.bank)
    mail_auth = {**(payload.get("auth") or {}), "from": payload.get("from", ""), "result": why}
    out = []
    for att in payload.get("attachments") or []:
        try:
            data = base64.b64decode(att.get("content_b64") or "", validate=True)
        except ValueError:
            continue
        if not data or len(data) > settings.STATEMENT_MAX_BYTES:
            continue
        try:
            out.append(_build(inbox.chama, route=Statement.Route.BANK_EMAIL,
                              filename=att.get("filename") or "statement",
                              data=data, mail_auth=mail_auth,
                              forced_state=None if ok else Statement.State.HELD_MAIL_CHECK))
        except Problem:
            continue  # duplicate file
    return out


def hook_token_ok(token: str) -> bool:
    expected = settings.MAIL_HOOK_TOKEN
    return bool(expected) and hmac.compare_digest(token, expected)
