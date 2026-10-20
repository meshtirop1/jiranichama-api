"""The bank mandate: who may sign at the bank, and how many must sign (protection plan
2026-10-20).

No bank in Kenya, Uganda or Tanzania lets an app approve a withdrawal for it, so the real
lock is at the bank: a mandate that says ANY TWO (or more) must sign. JiraniChama makes that
mandate visible and hard to fake:

- an official uploads the bank's mandate letter or form and names the signatories;
- a DIFFERENT official confirms it matches the paper, with an SMS code;
- the confirmed mandate is sealed on the ledger (the file's SHA-256 included) and shown to
  every member, with a warning if it lets one person sign alone;
- money out cannot be switched on without a confirmed mandate needing two signatures.
"""

import hashlib

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.core.problems import Problem
from apps.ledger import services as ledger

from .models import OFFICIAL_ROLES, BankMandate, Chama, Membership
from .services import active_bank_account

MIN_SIGNERS = 2
MAX_FILE = 10 * 1024 * 1024

# Bank products where every signatory approves each withdrawal on their own phone. Shown
# to officials as the strongest choice (research 2026-10-20).
MULTI_APPROVAL = {
    "KE": ["Equity Bank: Equitel Multiple Approval", "Postbank: M-Chama",
           "Bank of Africa: e-Chama"],
    "TZ": ["Vodacom: M-Koba"],
}


def _official(m: Membership) -> None:
    if m.role not in OFFICIAL_ROLES or not m.is_current:
        raise Problem("not_allowed", "Only an official does this.", status.HTTP_403_FORBIDDEN)


def current(chama: Chama) -> BankMandate | None:
    return (BankMandate.objects.filter(chama=chama, state=BankMandate.State.CONFIRMED)
            .order_by("-confirmed_at").first())


def propose(chama: Chama, by: Membership, *, signatory_ids: list, min_signers: int,
            bank_app_approvals: bool, filename: str, data: bytes) -> BankMandate:
    _official(by)
    account = active_bank_account(chama)
    if account is None:
        raise Problem("bank_account_missing", "Add the chama's bank account first.",
                      status.HTTP_409_CONFLICT)
    if not data or len(data) > MAX_FILE:
        raise Problem("file_invalid", "Attach a photo or PDF of the bank's mandate (up to "
                                      "10 MB).")
    signatories = list(Membership.objects.filter(chama=chama, pk__in=signatory_ids)
                       .exclude(state__in=[Membership.State.EXITED, Membership.State.DECEASED]))
    if len(signatories) != len(set(map(str, signatory_ids))):
        raise Problem("invalid_member", "Every signatory must be a member of this chama.")
    if len(signatories) < MIN_SIGNERS:
        raise Problem("too_few_signatories", "A chama account needs at least two "
                                             "signatories.")
    if not MIN_SIGNERS <= min_signers <= len(signatories):
        raise Problem("one_person_can_sign", "At least two signatories must sign every "
                                             "withdrawal. Ask the bank to change the "
                                             "mandate to 'any two to sign'.")
    if BankMandate.objects.filter(chama=chama, state=BankMandate.State.PROPOSED).exists():
        raise Problem("mandate_pending", "A mandate is already waiting for confirmation.",
                      status.HTTP_409_CONFLICT)
    digest = hashlib.sha256(data).hexdigest()
    with transaction.atomic():
        m = BankMandate(chama=chama, bank_account=account, min_signers=min_signers,
                        bank_app_approvals=bank_app_approvals, document_sha256=digest,
                        uploaded_by=by)
        m.document.save(f"{chama.id}-{digest[:16]}-{filename[-60:]}", ContentFile(data),
                        save=False)
        m.save()
        m.signatories.set(signatories)
    return m


def reject(m: BankMandate, by: Membership) -> BankMandate:
    _official(by)
    with transaction.atomic():
        m = BankMandate.objects.select_for_update().get(pk=m.pk)
        if m.state != BankMandate.State.PROPOSED:
            raise Problem("not_proposed", "This mandate is not waiting.",
                          status.HTTP_409_CONFLICT)
        m.state = BankMandate.State.REJECTED
        m.save(update_fields=["state"])
    return m


def require_confirmer(m: BankMandate, by: Membership) -> None:
    _official(by)
    if m.state != BankMandate.State.PROPOSED:
        raise Problem("not_proposed", "This mandate is not waiting for confirmation.",
                      status.HTTP_409_CONFLICT)
    if m.uploaded_by_id == by.id:
        raise Problem("second_official_required", "A different official must confirm the "
                                                  "mandate you uploaded.",
                      status.HTTP_403_FORBIDDEN)


def content_hash(m: BankMandate) -> str:
    from apps.core.canonical import canonical_bytes

    return hashlib.sha256(canonical_bytes({
        "mandate": str(m.id), "sha256": m.document_sha256, "min": m.min_signers,
        "signatories": sorted(str(s) for s in m.signatories.values_list("pk", flat=True)),
    })).hexdigest()


def start_confirm(m: BankMandate, by: Membership):
    from apps.devices import signing
    from apps.devices.models import SigningChallenge

    require_confirmer(m, by)
    names = ", ".join(s.member_code for s in m.signatories.order_by("member_code"))
    action = (f"CONFIRM the bank mandate: {m.min_signers} of {names} must sign"
              if by.user.language != "sw" else
              f"KUTHIBITISHA idhini ya benki: {m.min_signers} kati ya {names} watie sahihi")
    return signing.issue(user=by.user, purpose=SigningChallenge.Purpose.CONFIRM_MANDATE,
                         subject=f"chamas.mandate:{m.id}", content_hash=content_hash(m),
                         action_text=action[:120])


def confirm(m: BankMandate, by: Membership, *, challenge_id, sms_code: str) -> BankMandate:
    from apps.devices import signing
    from apps.devices.models import SigningChallenge

    require_confirmer(m, by)
    signing.confirm(user=by.user, challenge_id=challenge_id, code=sms_code,
                    purpose=SigningChallenge.Purpose.CONFIRM_MANDATE,
                    subject=f"chamas.mandate:{m.id}", content_hash=content_hash(m))
    with transaction.atomic():
        m = BankMandate.objects.select_for_update().get(pk=m.pk)
        require_confirmer(m, by)
        BankMandate.objects.filter(chama=m.chama, state=BankMandate.State.CONFIRMED).update(
            state=BankMandate.State.REPLACED)
        codes = sorted(m.signatories.values_list("member_code", flat=True))
        entry = ledger.append(
            chama=m.chama, type="bank_mandate", source="bank_mandate", source_id=m.id,
            decision={"signatories": codes, "min_signers": m.min_signers,
                      "bank_app_approvals": m.bank_app_approvals,
                      "document_sha256": m.document_sha256,
                      "uploaded_by": m.uploaded_by.member_code, "confirmed_by": by.member_code,
                      "account_last4": m.bank_account.account_no_last4})
        m.state, m.confirmed_by, m.confirmed_at, m.entry = (
            BankMandate.State.CONFIRMED, by, timezone.now(), entry)
        m.save(update_fields=["state", "confirmed_by", "confirmed_at", "entry"])
    return m


def require_safe_mandate(chama: Chama) -> None:
    """Money out needs a confirmed mandate where no one signs alone."""
    m = current(chama)
    if m is None:
        raise Problem("mandate_missing", "Add the bank's mandate first: who signs, and that "
                                         "at least two must sign.", status.HTTP_409_CONFLICT)
    if m.min_signers < MIN_SIGNERS:
        raise Problem("one_person_can_sign", "The bank mandate lets one person sign alone.",
                      status.HTTP_409_CONFLICT)


def as_dict(m: BankMandate | None) -> dict | None:
    if m is None:
        return None
    return {
        "id": str(m.id), "state": m.state, "min_signers": m.min_signers,
        "bank_app_approvals": m.bank_app_approvals,
        "signatories": [{"id": str(s.id), "member_code": s.member_code,
                         "name": s.user.display_name if s.user_id else "", "role": s.role}
                        for s in m.signatories.select_related("user").order_by("member_code")],
        "document_sha256": m.document_sha256,
        "uploaded_by": m.uploaded_by.member_code,
        "confirmed_by": m.confirmed_by.member_code if m.confirmed_by_id else None,
        "confirmed_at": m.confirmed_at.isoformat() if m.confirmed_at else None,
        "receipt": m.entry.receipt_code if m.entry_id else None,
    }
