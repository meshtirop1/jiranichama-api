"""Support access (5.16). See docs/flows/support-access.md.

By default staff see no chama's records. An official taps "Get help", chooses what support
may read and for how long (72 hours by default, 14 days at most), and a second official
confirms. Every member is told, and a support_access entry goes on the ledger. Access
ends by itself, or earlier when any official revokes it. Staff only ever read.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.chamas.models import OFFICIAL_ROLES, Chama, Membership
from apps.core.problems import Problem
from apps.ledger import services as ledger
from apps.notify.sms import SmsFailed, send_sms

from .models import SupportGrant

MAX_HOURS = 14 * 24


def _official(m: Membership) -> None:
    if m.role not in OFFICIAL_ROLES:
        raise Problem("not_allowed", "An official asks for help.", status.HTTP_403_FORBIDDEN)


def request(chama: Chama, by: Membership, *, areas: list, hours: int = 72,
            reason: str) -> SupportGrant:
    _official(by)
    areas = sorted(set(areas or []))
    if not areas or not set(areas) <= set(SupportGrant.Area.values):
        raise Problem("invalid", "Choose what support may read.")
    if not 1 <= hours <= MAX_HOURS:
        raise Problem("invalid", "Support access lasts 1 hour to 14 days.")
    reason = (reason or "").strip()[:300]
    if not reason:
        raise Problem("reason_required", "Say what you need help with.")
    if SupportGrant.objects.filter(chama=chama, state__in=[SupportGrant.State.REQUESTED,
                                                          SupportGrant.State.ACTIVE]).exists():
        raise Problem("already_sent", "Support access is already asked for or open.",
                      status.HTTP_409_CONFLICT)
    return SupportGrant.objects.create(chama=chama, areas=areas, hours=hours, reason=reason,
                                       requested_by=by)


def _tell_members(g: SupportGrant) -> None:
    until = timezone.localtime(g.ends_at).strftime("%d %b %H:%M")
    for m in Membership.objects.filter(chama=g.chama, state=Membership.State.ACTIVE) \
            .select_related("user"):
        text = {"en": f"{g.chama.name}: the officials gave JiraniChama support read-only "
                      f"access ({', '.join(g.areas)}) until {until}.",
                "sw": f"{g.chama.name}: viongozi wameipa huduma ya JiraniChama ruhusa ya "
                      f"kusoma tu ({', '.join(g.areas)}) hadi {until}."}
        try:
            send_sms(m.user.phone, text.get(m.user.language, text["en"]))
        except SmsFailed:
            continue


def confirm(g: SupportGrant, by: Membership) -> SupportGrant:
    _official(by)
    if by.id == g.requested_by_id:
        raise Problem("second_official_required", "A different official confirms it.",
                      status.HTTP_403_FORBIDDEN)
    with transaction.atomic():
        g = SupportGrant.objects.select_for_update().get(pk=g.pk)
        if g.state != SupportGrant.State.REQUESTED:
            raise Problem("already_answered", "This is no longer waiting.",
                          status.HTTP_409_CONFLICT)
        now = timezone.now()
        g.state, g.confirmed_by = SupportGrant.State.ACTIVE, by
        g.starts_at, g.ends_at = now, now + timedelta(hours=g.hours)
        g.save(update_fields=["state", "confirmed_by", "starts_at", "ends_at"])
        ledger.append(chama=g.chama, type="support_access", source="support", source_id=g.id,
                      decision={"areas": g.areas, "until": g.ends_at.isoformat(),
                                "reason": g.reason,
                                "requested_by": g.requested_by.member_code,
                                "confirmed_by": by.member_code})
    transaction.on_commit(lambda: _tell_members(g))
    return g


def revoke(g: SupportGrant, by: Membership) -> SupportGrant:
    _official(by)
    if g.state not in (SupportGrant.State.REQUESTED, SupportGrant.State.ACTIVE):
        raise Problem("already_answered", "This has already ended.", status.HTTP_409_CONFLICT)
    was_active = g.state == SupportGrant.State.ACTIVE
    g.state, g.revoked_by = SupportGrant.State.REVOKED, by
    g.ends_at = timezone.now() if was_active else g.ends_at
    g.save(update_fields=["state", "revoked_by", "ends_at"])
    if was_active:
        ledger.append(chama=g.chama, type="support_access", source="support", source_id=g.id,
                      decision={"revoked_by": by.member_code})
    return g


def expire_due() -> int:
    return SupportGrant.objects.filter(state=SupportGrant.State.ACTIVE,
                                       ends_at__lte=timezone.now()) \
        .update(state=SupportGrant.State.EXPIRED)


def active_grant(chama, area: str | None = None) -> SupportGrant | None:
    expire_due()
    qs = SupportGrant.objects.filter(chama=chama, state=SupportGrant.State.ACTIVE,
                                     ends_at__gt=timezone.now())
    for g in qs:
        if area is None or area in g.areas:
            return g
    return None
