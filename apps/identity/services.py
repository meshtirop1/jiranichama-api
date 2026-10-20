"""ID checks (KYC), paid by the chama (protection plan 2026-10-20).

- One person, one ID: a verified ID (keyed hash) belongs to one account only, so nobody
  can be two members of the same chama, or vote twice under two phones.
- Officials must pass before money can go out (rule P.3), once checks are switched on
  (``KYC_ENFORCED``).
- Other members see only "ID checked"; never the number, never a photo.
"""

import re
import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.core.crypto import keyed_hash
from apps.core.problems import Problem

from . import smile
from .models import IdentityCheck


def id_hash(country: str, id_type: str, id_number: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9]", "", id_number).upper()
    return keyed_hash("national_id", f"{country}:{id_type}:{clean}")


def latest(user) -> IdentityCheck | None:
    return IdentityCheck.objects.filter(user=user).order_by("-created_at").first()


def status_of(user) -> str:
    c = latest(user)
    return c.status if c else "not_started"


def verified_user_ids(user_ids) -> set:
    return set(IdentityCheck.objects.filter(user_id__in=user_ids,
                                            status=IdentityCheck.Status.VERIFIED)
               .values_list("user_id", flat=True))


def _words(name: str) -> set[str]:
    return {w for w in re.sub(r"[^a-z ]", " ", name.lower()).split() if len(w) > 1}


def names_match(a: str, b: str) -> bool:
    """At least two words in common (or the only word), ignoring order and case: "Wanjiru
    Kamau" matches "KAMAU WANJIRU MARY"."""
    wa, wb = _words(a), _words(b)
    common = wa & wb
    return len(common) >= 2 or (bool(common) and min(len(wa), len(wb)) == 1)


def start(user, *, country: str, id_type: str, id_number: str, first_name: str,
          last_name: str, paid_by=None) -> IdentityCheck:
    if not smile.available():
        raise Problem("kyc_not_available", "ID checks are not switched on yet.",
                      status.HTTP_503_SERVICE_UNAVAILABLE)
    if id_type not in smile.ID_TYPES.get(country, []):
        raise Problem("id_type_invalid", "This kind of ID cannot be checked in this country.")
    digits = re.sub(r"[^A-Za-z0-9]", "", id_number)
    if not 5 <= len(digits) <= 20:
        raise Problem("id_number_invalid", "Type the ID number as it is on the card.")
    if status_of(user) in (IdentityCheck.Status.VERIFIED, IdentityCheck.Status.PENDING):
        raise Problem("kyc_done", "Your ID is already checked or being checked.",
                      status.HTTP_409_CONFLICT)
    h = id_hash(country, id_type, digits)
    if IdentityCheck.objects.filter(id_hash=h, status=IdentityCheck.Status.VERIFIED) \
            .exclude(user=user).exists():
        # One ID, one person: this ID is already on another JiraniChama account.
        raise Problem("id_already_used", "This ID is already checked on another account. "
                                         "Ask your officials for help.",
                      status.HTTP_409_CONFLICT)
    if paid_by is not None and not paid_by.memberships.filter(user=user).exists():
        raise Problem("not_member", "Choose one of your own chamas to pay for the check.")
    with transaction.atomic():
        check = IdentityCheck.objects.create(
            user=user, country=country, id_type=id_type, id_hash=h, last4=digits[-4:],
            paid_by=paid_by, price_usd_cents=getattr(settings, "KYC_PRICE_USD_CENTS", 0),
            job_id=uuid.uuid4().hex)
    try:
        r = smile.lookup(country=country, id_type=id_type, id_number=digits,
                         first_name=first_name, last_name=last_name, job_id=check.job_id,
                         user_id=str(user.id))
    except Exception:  # the provider did not answer: the check stays pending, try later
        return check
    return decide(check, verified=r["verified"], code=r["code"], registry_name=r["full_name"])


def decide(check: IdentityCheck, *, verified: bool, code: str, registry_name: str
           ) -> IdentityCheck:
    """Record the provider's answer. A verified ID must also match the person's name."""
    name_ok = names_match(check.user.display_name, registry_name) if registry_name else False
    ok = verified and name_ok
    IdentityCheck.objects.filter(pk=check.pk).update(
        status=IdentityCheck.Status.VERIFIED if ok else IdentityCheck.Status.FAILED,
        result_code=code[:10], registry_name=registry_name[:160], name_matches=name_ok,
        decided_at=timezone.now())
    check.refresh_from_db()
    return check


def require_officials_verified(chama) -> None:
    """Rule P.3: money goes out only after every official has passed the ID check. Only
    once ID checks are switched on for real (KYC_ENFORCED), so chamas are not stuck
    before the provider is set up."""
    if not getattr(settings, "KYC_ENFORCED", False):
        return
    from apps.chamas.models import OFFICIAL_ROLES

    officials = list(chama.memberships.filter(role__in=OFFICIAL_ROLES)
                     .values_list("user_id", flat=True))
    missing = set(officials) - verified_user_ids(officials)
    if missing:
        raise Problem("officials_not_verified", "Every official must pass the ID check "
                                                "before money can go out (rule P.3).",
                      status.HTTP_409_CONFLICT)
