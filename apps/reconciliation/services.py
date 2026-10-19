"""From a bank credit to a recorded contribution (5.3).

Matching: the member code in the reference or narration, then the payer's phone if it
belongs to exactly one member of this chama, otherwise the treasurer's unmatched queue.
Amount and timing hints are shown to the treasurer but never decide on their own.
"""

import re

from django.db import transaction
from rest_framework import status

from apps.accounts.phones import InvalidPhone, normalise_phone, phone_hash
from apps.chamas.models import Chama, Membership
from apps.contributions.projections import dues, open_fines
from apps.core.problems import Problem
from apps.feeds.models import BankTransaction
from apps.fines.models import Fine
from apps.ledger import services as ledger
from apps.ledger.models import LedgerEntry
from apps.rules import engine
from apps.rules.models import version_at

from .models import Match

_MEMBER_CODE = re.compile(r"JC[\s\-_]?(\d{4})[\s\-_]?(\d{3})", re.I)
_PHONE = re.compile(r"(?<!\d)(?:\+?254|0)([17]\d{8})(?!\d)")


def find_member(chama: Chama, text: str, payer_phone_hash: str = ""
                ) -> tuple[Membership | None, str | None]:
    current = [Membership.State.ACTIVE, Membership.State.PROBATION]
    for m in _MEMBER_CODE.finditer(text or ""):
        code = f"JC-{m.group(1)}-{m.group(2)}"
        if not code.startswith(chama.member_code_prefix + "-"):
            continue  # another chama's code: never record it here (5.3)
        member = Membership.objects.filter(chama=chama, member_code=code,
                                           state__in=current).first()
        if member:
            return member, Match.Method.MEMBER_CODE

    hashes = {payer_phone_hash} if payer_phone_hash else set()
    for m in _PHONE.finditer(text or ""):
        try:
            hashes.add(phone_hash(normalise_phone(m.group(0))))
        except InvalidPhone:
            continue
    for h in hashes:
        found = list(Membership.objects.filter(chama=chama, user__phone_hash=h,
                                               state__in=current)[:2])
        if len(found) == 1:
            return found[0], Match.Method.PHONE
    return None, None


def match_credit(txn: BankTransaction) -> BankTransaction:
    """Try to match a new credit automatically; otherwise leave it for the treasurer."""
    if txn.direction != BankTransaction.Direction.CREDIT or \
            txn.state != BankTransaction.State.UNMATCHED:
        return txn
    member, method = find_member(txn.chama, txn.narration, txn.payer_phone_hash)
    if member is not None:
        try:
            record(txn, member, method=method)
        except Problem:
            pass  # e.g. paid before the rules existed: the treasurer decides
    return txn


@transaction.atomic
def record(txn: BankTransaction, member: Membership, *, method: str, decided_by=None,
           reason: str = "") -> list[LedgerEntry]:
    """Judge the payment under the rules in force on its bank date, and write the
    ledger entries the rules produce."""
    txn = BankTransaction.objects.select_for_update().get(pk=txn.pk)
    if txn.state != BankTransaction.State.UNMATCHED:
        raise Problem("already_recorded", "This payment has already been recorded.",
                      status.HTTP_409_CONFLICT)
    if member.chama_id != txn.chama_id:
        raise Problem("wrong_chama", "That member is not in this chama.")
    version = version_at(txn.chama, txn.value_date)
    if version is None:
        raise Problem("no_rules_on_date", "The chama's rules did not exist on the payment "
                                          "date yet.", status.HTTP_409_CONFLICT)

    match = Match.objects.create(bank_txn=txn, membership=member, method=method,
                                 decided_by=decided_by, reason=reason)
    entries = apply_payment(txn=txn, member=member, amount_cents=txn.amount_cents,
                            value_date=txn.value_date, version=version,
                            base={"bank_ref": txn.external_ref,
                                  "value_date": txn.value_date.isoformat(),
                                  "method": method, "match_id": match.id,
                                  **({"typed_in": True} if txn.typed_by_id else {})})
    txn.state = BankTransaction.State.RECORDED
    txn.save(update_fields=["state"])
    return entries


def apply_payment(*, txn: BankTransaction, member: Membership, amount_cents: int, value_date,
                  version, base: dict) -> list[LedgerEntry]:
    """Split one payment (a bank line, or one member's share of banked cash) by the rules
    in force on ``value_date``, and write the entries. Called inside a transaction."""
    allocation = engine.allocate(rules=version.rules, amount_cents=amount_cents,
                                 value_date=value_date, dues=dues(member, value_date),
                                 fines=open_fines(member))
    entries = []
    for s in allocation.slices:
        if s.kind == "contribution":
            entries.append(ledger.append(
                chama=txn.chama, type="contribution", source="bank_txn", source_id=txn.id,
                member=member, amount_cents=s.amount_cents, period=s.period,
                rule_version_id=version.id, rule_refs=s.rule_refs,
                decision={**base, "outcome": s.outcome}))
        else:
            fine = Fine.objects.select_for_update().get(pk=s.fine_id)
            entries.append(ledger.append(
                chama=txn.chama, type="fine_paid", source="bank_txn", source_id=txn.id,
                member=member, amount_cents=s.amount_cents, period=s.period,
                rule_version_id=version.id, rule_refs=s.rule_refs,
                decision={**base, "fine_id": str(fine.id)}))
            fine.paid_cents += s.amount_cents
            if fine.paid_cents >= fine.amount_cents:
                fine.state = Fine.State.PAID
            fine.save(update_fields=["paid_cents", "state"])
    for fine_id in allocation.fines_to_reverse:
        fine = Fine.objects.select_for_update().get(pk=fine_id)
        entries.append(ledger.append(
            chama=txn.chama, type="reversal", source="bank_txn", source_id=txn.id,
            member=member, amount_cents=-fine.amount_cents, period=fine.period,
            reverses=fine.raised_entry, rule_version_id=version.id,
            rule_refs=[engine.REF_LATE_FINE],
            decision={**base, "reason": "paid on time; the bank record reached us late",
                      "fine_id": str(fine.id)}))
        fine.state = Fine.State.REVERSED
        fine.save(update_fields=["state"])
    return entries


def assign(txn: BankTransaction, member: Membership, *, by: Membership, reason: str
           ) -> list[LedgerEntry]:
    if by.chama_id != txn.chama_id or by.role != Membership.Role.TREASURER:
        raise Problem("not_allowed", "Only the treasurer assigns unmatched payments.",
                      status.HTTP_403_FORBIDDEN)
    if not (reason or "").strip():
        raise Problem("reason_required", "Say why this payment belongs to this member.")
    return record(txn, member, method=Match.Method.TREASURER, decided_by=by.user,
                  reason=reason.strip())


@transaction.atomic
def not_me(txn: BankTransaction, member: Membership, *, reason: str = "") -> list[LedgerEntry]:
    """The member a payment was assigned to says it was not theirs (5.3). Every entry the
    payment produced is reversed by a new entry, and the payment goes back to the queue.
    Phase 3 also opens a dispute."""
    txn = BankTransaction.objects.select_for_update().get(pk=txn.pk)
    last = txn.matches.order_by("-at", "-id").first()
    if txn.state != BankTransaction.State.RECORDED or last is None or \
            last.membership_id != member.id or last.method != Match.Method.TREASURER:
        raise Problem("not_yours", "Only a payment the treasurer assigned to you can be "
                                   "sent back.", status.HTTP_409_CONFLICT)
    # Only the entries this assignment produced; earlier rounds were reversed already.
    produced = LedgerEntry.objects.filter(source="bank_txn", source_id=txn.id, member=member,
                                          decision__match_id=last.id).order_by("seq")
    entries = []
    for entry in produced:
        entries.append(ledger.append(
            chama=txn.chama, type="reversal", source="bank_txn", source_id=txn.id,
            member=member, amount_cents=-entry.amount_cents, period=entry.period,
            reverses=entry, rule_version_id=entry.rule_version_id, rule_refs=entry.rule_refs,
            decision={"reason": "member says this payment was not theirs",
                      "note": reason.strip()}))
        fine_id = entry.decision.get("fine_id")
        if fine_id:
            fine = Fine.objects.select_for_update().get(pk=fine_id)
            if entry.type == "fine_paid":
                fine.paid_cents -= entry.amount_cents
            # Undoing a payment, or undoing a "raised in error" reversal, both leave the
            # fine standing again: paid if it is still fully paid, otherwise owed.
            fine.state = Fine.State.PAID if fine.paid_cents >= fine.amount_cents \
                else Fine.State.RAISED
            fine.save(update_fields=["paid_cents", "state"])
    Match.objects.create(bank_txn=txn, membership=member, method=Match.Method.NOT_ME,
                         decided_by=member.user, reason=reason.strip())
    txn.state = BankTransaction.State.UNMATCHED
    txn.save(update_fields=["state"])
    return entries
