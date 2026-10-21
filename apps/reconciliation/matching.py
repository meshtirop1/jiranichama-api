"""Smart matching (owner request 2026-10-21): fewer payments wait for the treasurer.

1. **Learned payers.** When the treasurer assigns a payment from a phone that is not a
   member's (a husband's, a child's, a shop's), the member is asked once: "should
   payments from this number count for you?". If they say yes, the next ones match by
   themselves (``Match.Method.PAYER_LINK``). The member, or an official, can end it.
2. **Suggestions.** For a payment still waiting, the treasurer sees up to three likely
   members, each with its reasons: a member code typed with a slip, the payer's name, the
   usual amount, a number that paid for them before. The treasurer confirms with one tap.
   A suggestion never records a payment on its own: only a real member code, the member's
   own phone or a confirmed payer counts automatically.
"""

import re

from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.chamas.models import Chama, Membership
from apps.core.problems import Problem
from apps.feeds.models import BankTransaction
from apps.ledger.models import LedgerEntry

from .models import Match, PayerLink

CURRENT = [Membership.State.ACTIVE, Membership.State.PROBATION]

# Words in bank and M-Pesa narrations that are never a person's name.
_NOT_NAMES = {"MPESA", "M-PESA", "PESA", "DEPOSIT", "PAYMENT", "PAY", "TRANSFER", "FROM", "TO",
              "REF", "CASH", "ACC", "ACCOUNT", "JC", "CHAMA", "BANK", "AGENT", "TILL",
              "PAYBILL", "NOW", "THE", "AND", "FOR", "LTD", "KES", "EFT", "RTGS", "PESALINK",
              "MOBILE", "BRANCH", "CDM", "ATM", "CONTRIBUTION", "MONTHLY", "SAVINGS"}
_DIGITS = re.compile(r"\d[\d\s\-_]{4,12}\d")


def _current(chama: Chama):
    return Membership.objects.filter(chama=chama, state__in=CURRENT).select_related("user")


def linked_member(chama: Chama, hashes: set[str]) -> Membership | None:
    """A confirmed payer link for one of these numbers."""
    for h in hashes:
        link = (PayerLink.objects.filter(chama=chama, phone_hash=h,
                                         state=PayerLink.State.CONFIRMED)
                .select_related("membership").order_by("-created_at").first())
        if link and link.membership.state in CURRENT:
            return link.membership
    return None


# --- Suggestions --------------------------------------------------------------------------

def _close_enough(a: str, b: str) -> bool:
    """One digit wrong, missing, extra, or two swapped."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return (len(diff) == 2 and diff[1] == diff[0] + 1
                and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def _names(text: str) -> set[str]:
    return {w for w in re.findall(r"[A-Z][A-Z'\-]{2,}", (text or "").upper())
            if w not in _NOT_NAMES}


def _usual_amounts(m: Membership) -> set[int]:
    amounts = set(LedgerEntry.objects.filter(member=m, type="contribution",
                                             source="bank_txn")
                  .order_by("-seq").values_list("amount_cents", flat=True)[:6])
    from apps.automation.reminders import owed

    due = owed(m, timezone.localdate())
    amounts |= {due["total_cents"], due["current_cents"]}
    amounts.discard(0)
    return amounts


def suggest(txn: BankTransaction, limit: int = 3) -> list[dict]:
    if txn.direction != BankTransaction.Direction.CREDIT:
        return []
    text = txn.narration or ""
    names = _names(text)
    digit_runs = {re.sub(r"\D", "", d) for d in _DIGITS.findall(text)}
    prefix_digits = re.sub(r"\D", "", txn.chama.member_code_prefix)
    earlier = set()
    if txn.payer_phone_hash:
        earlier = set(Match.objects.filter(
            bank_txn__chama=txn.chama, bank_txn__payer_phone_hash=txn.payer_phone_hash,
            method__in=[Match.Method.TREASURER, Match.Method.PAYER_LINK])
            .exclude(bank_txn=txn).values_list("membership_id", flat=True))
    out = []
    for m in _current(txn.chama):
        score, reasons = 0, []
        code_digits = re.sub(r"\D", "", m.member_code)
        member_no = code_digits[len(prefix_digits):]
        if any(_close_enough(d, code_digits) for d in digit_runs if len(d) >= 6) or any(
                d.startswith(prefix_digits) and _close_enough(d[len(prefix_digits):],
                                                              member_no)
                for d in digit_runs):
            score += 50
            reasons.append("code")
        shared = names & _names(m.user.display_name)
        if shared:
            score += min(len(shared), 2) * 25
            reasons.append("name")
        if m.id in earlier:
            score += 40
            reasons.append("paid_before")
        if txn.amount_cents in _usual_amounts(m):
            score += 15
            reasons.append("amount")
        if score >= 25:
            out.append({"membership_id": str(m.id), "member_code": m.member_code,
                        "name": m.user.display_name or m.member_code, "score": min(score, 99),
                        "reasons": reasons})
    out.sort(key=lambda s: -s["score"])
    return out[:limit]


def reason_text(s: dict) -> str:
    words = {"code": "member code with a slip", "name": "the payer's name",
             "paid_before": "this number paid for them before", "amount": "their usual amount"}
    return "Suggested: " + ", ".join(words[r] for r in s["reasons"])


# --- Learning a payer --------------------------------------------------------------------

def _owner_is_member(chama: Chama, h: str) -> bool:
    return Membership.objects.filter(chama=chama, user__phone_hash=h).exists()


def learn_from_assignment(txn: BankTransaction, member: Membership) -> PayerLink | None:
    """After the treasurer assigns a payment from a number that is not a member's own, ask
    the member whether that number pays for them."""
    from apps.automation.notices import notify

    h = txn.payer_phone_hash
    if not h or _owner_is_member(txn.chama, h):
        return None
    if PayerLink.objects.filter(chama=txn.chama, phone_hash=h,
                                state__in=PayerLink.LIVE).exists():
        return None
    link = PayerLink.objects.create(chama=txn.chama, membership=member, phone_hash=h,
                                    from_txn=txn)
    transaction.on_commit(lambda: notify(
        member.user, chama=txn.chama, kind="payer", key=f"payer-link:{link.id}", sms="away",
        data={"screen": "payer_link", "id": str(link.id)},
        text={"en": "A payment from a phone that is not yours was counted for you. Should "
                    "payments from that number always count for you? Answer in JiraniChama.",
              "sw": "Malipo kutoka simu isiyo yako yamehesabiwa kwako. Je, malipo kutoka "
                    "namba hiyo yahesabiwe kwako kila mara? Jibu kwenye JiraniChama."}))
    return link


def _require_member_of(link: PayerLink, by: Membership) -> None:
    if by.chama_id != link.chama_id:
        raise Problem("not_found", "Not found.", status.HTTP_404_NOT_FOUND)


def answer(link: PayerLink, by: Membership, *, yes: bool) -> PayerLink:
    _require_member_of(link, by)
    if by.id != link.membership_id:
        raise Problem("not_yours", "Only the member it is about answers.",
                      status.HTTP_403_FORBIDDEN)
    if link.state != PayerLink.State.SUGGESTED:
        raise Problem("already_answered", "This was answered already.", status.HTTP_409_CONFLICT)
    link.state = PayerLink.State.CONFIRMED if yes else PayerLink.State.DECLINED
    link.changed_at = timezone.now()
    link.save(update_fields=["state", "changed_at"])
    return link


def end(link: PayerLink, by: Membership) -> PayerLink:
    _require_member_of(link, by)
    if by.id != link.membership_id and not by.is_official:
        raise Problem("not_allowed", "The member or an official can end this.",
                      status.HTTP_403_FORBIDDEN)
    if link.state not in PayerLink.LIVE:
        raise Problem("already_ended", "This has ended already.", status.HTTP_409_CONFLICT)
    link.state, link.changed_at = PayerLink.State.ENDED, timezone.now()
    link.save(update_fields=["state", "changed_at"])
    return link


def as_dict(link: PayerLink) -> dict:
    return {"id": str(link.id), "membership_id": str(link.membership_id),
            "member_code": link.membership.member_code,
            "name": link.membership.user.display_name or link.membership.member_code,
            "state": link.state, "phone_hint": link.phone_hint,
            "since": link.created_at.isoformat(),
            "from_amount_cents": link.from_txn.amount_cents if link.from_txn_id else None}
