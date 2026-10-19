"""Who may do what in a chama. Roles exist per chama (Part 3)."""

from rest_framework import status

from apps.core.problems import Problem

from .models import OFFICIAL_ROLES, Chama, Membership


def membership_of(user, chama: Chama) -> Membership | None:
    return (Membership.objects.filter(chama=chama, user=user)
            .exclude(state=Membership.State.EXITED).first())


def require_member(user, chama: Chama) -> Membership:
    m = membership_of(user, chama)
    # A member serving their notice (5.13) still sees the chama until they have left.
    if m is None or not (m.is_current or m.state == Membership.State.EXITING):
        # Same answer as "no such chama", so membership is not leaked.
        raise Problem("not_found", "Chama not found.", status.HTTP_404_NOT_FOUND)
    return m


def require_official(user, chama: Chama, roles=OFFICIAL_ROLES + ("auditor",)) -> Membership:
    m = require_member(user, chama)
    if m.role not in roles:
        raise Problem("not_allowed", "Only an official can do this.", status.HTTP_403_FORBIDDEN)
    return m


def require_founder(user, chama: Chama) -> Membership:
    m = require_member(user, chama)
    if chama.created_by_id != user.id:
        raise Problem("not_allowed", "Only the founder can do this during setup.",
                      status.HTTP_403_FORBIDDEN)
    return m


def officials(chama: Chama):
    return Membership.objects.filter(chama=chama, role__in=OFFICIAL_ROLES,
                                     state=Membership.State.ACTIVE)
