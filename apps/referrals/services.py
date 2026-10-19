"""Referrals to licensed partners (4.10, decision 18). See docs/flows/referrals.md.

Only to partners licensed by the CBK, SASRA or IRA, and only with the chama's recorded
consent: an official proposes it, the members vote, and the result is on the ledger.
What the partner then receives is the chama's name, its type and size, and the contact
official's name and phone, nothing else. Consent can be withdrawn until it is sent.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.chamas.models import OFFICIAL_ROLES, Chama, Membership
from apps.core.problems import Problem
from apps.ledger import services as ledger
from apps.rules.models import version_at

from .models import Partner, Referral

VOTE_DAYS = 7


def propose(chama: Chama, by: Membership, *, partner_id, reason: str,
            contact_id=None) -> Referral:
    from apps.votes import services as votes

    if by.role not in OFFICIAL_ROLES:
        raise Problem("not_allowed", "An official proposes a referral.",
                      status.HTTP_403_FORBIDDEN)
    partner = Partner.objects.filter(pk=partner_id, active=True).first()
    if partner is None:
        raise Problem("invalid", "Choose a partner from the list.")
    reason = (reason or "").strip()[:300]
    if not reason:
        raise Problem("reason_required", "Say why the chama should be introduced.")
    contact = by
    if contact_id:
        contact = Membership.objects.filter(chama=chama, pk=contact_id,
                                            role__in=OFFICIAL_ROLES).first()
        if contact is None:
            raise Problem("invalid_member", "The contact is one of the officials.")
    if Referral.objects.filter(chama=chama, partner=partner,
                               state__in=[Referral.State.VOTING,
                                          Referral.State.CONSENTED]).exists():
        raise Problem("already_sent", "A referral to this partner is already under way.",
                      status.HTTP_409_CONFLICT)
    with transaction.atomic():
        r = Referral.objects.create(chama=chama, partner=partner, proposed_by=by,
                                    contact=contact, reason=reason)
        v = votes.open_vote(
            chama, subject=f"referrals.referral:{r.id}",
            question=f"Agree to be introduced to {partner.name} "
                     f"({partner.get_product_display()}): {reason[:120]}?",
            rules=version_at(chama, timezone.localdate()).rules,
            closes_at=timezone.now() + timedelta(days=VOTE_DAYS))
        r.vote_id = v.id
        r.save(update_fields=["vote_id"])
    return r


def on_vote_closed(vote) -> None:
    r = Referral.objects.select_for_update().get(pk=vote.subject.split(":", 1)[1])
    if r.state != Referral.State.VOTING:
        return
    if vote.state != "passed":
        r.state = Referral.State.REFUSED
        r.save(update_fields=["state"])
        return
    p = r.partner
    r.consent_entry = ledger.append(
        chama=r.chama, type="referral_consent", source="referral", source_id=r.id,
        decision={"partner": p.name, "regulator": p.regulator,
                  "licence_number": p.licence_number, "product": p.product,
                  "contact": r.contact.member_code, "vote": str(vote.id),
                  "shared": ["chama name", "type", "members", "contact name and phone"]})
    r.state = Referral.State.CONSENTED
    r.save(update_fields=["consent_entry", "state"])


def withdraw(r: Referral, by: Membership) -> Referral:
    if by.role not in OFFICIAL_ROLES or by.chama_id != r.chama_id:
        raise Problem("not_allowed", "An official withdraws the consent.",
                      status.HTTP_403_FORBIDDEN)
    if r.state not in (Referral.State.VOTING, Referral.State.CONSENTED):
        raise Problem("already_sent", "It has been sent, or was never agreed.",
                      status.HTTP_409_CONFLICT)
    r.state = Referral.State.WITHDRAWN
    r.save(update_fields=["state"])
    ledger.append(chama=r.chama, type="referral_consent", source="referral", source_id=r.id,
                  decision={"partner": r.partner.name, "withdrawn_by": by.member_code})
    return r


def handover(r: Referral) -> dict:
    """What staff pass to the partner, and only for a consented referral."""
    if r.state != Referral.State.CONSENTED:
        raise Problem("no_consent", "The chama has not consented.", status.HTTP_409_CONFLICT)
    c = r.chama
    return {"chama": c.name, "type": c.type,
            "members": c.memberships.filter(state=Membership.State.ACTIVE).count(),
            "contact_name": r.contact.user.display_name or r.contact.member_code,
            "contact_phone": r.contact.user.phone, "product": r.partner.product}


def mark_sent(r: Referral) -> Referral:
    handover(r)  # refuses without consent
    r.state, r.sent_at = Referral.State.SENT, timezone.now()
    r.save(update_fields=["state", "sent_at"])
    return r
