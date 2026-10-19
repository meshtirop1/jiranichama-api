"""Creating a chama, inviting people, and adopting the first rule book (5.1, 5.2).

States: setup → awaiting_officials → awaiting_adoption → active (records-only), plus
expired. Money-out mode is unlocked later, per chama, when every official has passed
the ID check and registered a signing key (Phase 2).
"""

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status

from apps.accounts.phones import normalise_phone, phone_hash, phone_hint
from apps.core.canonical import canonical_bytes
from apps.core.problems import Problem
from apps.devices import signing
from apps.devices.models import SigningChallenge
from apps.ledger import services as ledger
from apps.rules import validation
from apps.rules.models import RuleVersion, rules_hash
from apps.rules.templates import TEMPLATE_VERSION, template_for

from .models import (
    OFFICIAL_ROLES,
    BankAccount,
    Chama,
    Invite,
    Membership,
    StatementInbox,
)

SETUP_DAYS = 14
INVITE_DAYS = 14
_INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # no I or O, which read as 1 and 0


def _today():
    return timezone.localdate()


def _content_hash(obj: dict) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def _new_prefix() -> str:
    for _ in range(50):
        prefix = f"JC-{secrets.randbelow(9000) + 1000}"
        if not Chama.objects.filter(member_code_prefix=prefix).exists():
            return prefix
    raise RuntimeError("could not allocate a member-code prefix")


def _new_invite_code() -> str:
    for _ in range(50):
        code = "".join(secrets.choice(_INVITE_ALPHABET) for _ in range(6))
        if not Invite.objects.filter(code=code).exists():
            return code
    raise RuntimeError("could not allocate an invite code")


def _next_member_code(chama: Chama) -> str:
    """Called with the chama row locked."""
    code = f"{chama.member_code_prefix}-{chama.next_member_no:03d}"
    chama.next_member_no += 1
    chama.save(update_fields=["next_member_no"])
    return code


def _lock(chama: Chama) -> Chama:
    return Chama.objects.select_for_update().get(pk=chama.pk)


# --- Setup ------------------------------------------------------------------------------

@transaction.atomic
def create_chama(*, founder, name: str, type: str, founder_role: str,
                 expected_members: int = 0, meets: str = Chama.Meets.MONTHLY) -> Chama:
    if founder_role not in OFFICIAL_ROLES + (Membership.Role.MEMBER,):
        raise Problem("invalid_role", "Pick chair, treasurer, secretary or member.")
    chama = Chama.objects.create(
        name=name.strip(), type=type, member_code_prefix=_new_prefix(),
        expected_members=expected_members, meets=meets, created_by=founder,
        draft_rules=template_for(type), draft_template=f"{type}@{TEMPLATE_VERSION}",
        setup_expires_at=timezone.now() + timedelta(days=SETUP_DAYS),
    )
    digits = chama.member_code_prefix.split("-")[1]
    StatementInbox.objects.create(
        chama=chama,
        address=f"jc-{digits}-{secrets.token_hex(3)}@{settings.STATEMENT_INBOX_DOMAIN}",
    )
    chama = _lock(chama)
    Membership.objects.create(chama=chama, user=founder, role=founder_role,
                              member_code=_next_member_code(chama))
    return chama


def _require_setup(chama: Chama) -> None:
    if not chama.in_setup:
        raise Problem("not_in_setup", "This can only be changed while the chama is in setup.",
                      status.HTTP_409_CONFLICT)


@transaction.atomic
def set_bank_account(chama: Chama, *, bank: str, account_name: str, account_no: str,
                     paybill: str = "", till: str = "", statement_route: str) -> BankAccount:
    chama = _lock(chama)
    _require_setup(chama)
    digits = BankAccount.normalise_account_no(account_no)
    if not 6 <= len(digits) <= 20:
        raise Problem("invalid_account_no", "Enter the account number as it appears on "
                                            "the bank statement.")
    BankAccount.objects.filter(chama=chama, active_to__isnull=True).update(
        active_to=timezone.now())
    account = BankAccount(chama=chama, bank=bank, account_name=account_name.strip(),
                          paybill=paybill.strip(), till=till.strip(),
                          statement_route=statement_route)
    account.set_account_no(digits)
    account.save()
    return account


def active_bank_account(chama: Chama) -> BankAccount | None:
    return BankAccount.objects.filter(chama=chama, active_to__isnull=True).first()


@transaction.atomic
def set_draft_rules(chama: Chama, rules: dict) -> dict:
    chama = _lock(chama)
    _require_setup(chama)
    validation.validate(rules)
    chama.draft_rules = rules
    chama.save(update_fields=["draft_rules"])
    return rules


# --- Invites ----------------------------------------------------------------------------

@transaction.atomic
def invite(chama: Chama, *, invited_by, role: str = Membership.Role.MEMBER,
           phone: str | None = None) -> Invite:
    if chama.state in (Chama.State.CLOSED, Chama.State.EXPIRED, Chama.State.SUSPENDED):
        raise Problem("chama_closed", "This chama is not accepting members.",
                      status.HTTP_409_CONFLICT)
    if role in OFFICIAL_ROLES and not phone:
        raise Problem("phone_required", "Officials are invited by phone number.")
    ph = hint = ""
    if phone:
        e164 = normalise_phone(phone)
        ph, hint = phone_hash(e164), phone_hint(e164)
        if Membership.objects.filter(chama=chama, user__phone_hash=ph).exclude(
                state=Membership.State.EXITED).exists():
            raise Problem("already_member", "This phone number already belongs to a member.",
                          status.HTTP_409_CONFLICT)
    if role in OFFICIAL_ROLES and Membership.objects.filter(
            chama=chama, role=role, state=Membership.State.ACTIVE).exists():
        raise Problem("role_taken", "Someone already holds this office.",
                      status.HTTP_409_CONFLICT)
    return Invite.objects.create(chama=chama, code=_new_invite_code(), phone_hash=ph,
                                 phone_hint=hint, role=role, invited_by=invited_by,
                                 expires_at=timezone.now() + timedelta(days=INVITE_DAYS))


def _rules_for_joining(chama: Chama) -> dict:
    current = RuleVersion.objects.filter(chama=chama, state=RuleVersion.State.IN_FORCE) \
        .order_by("-version_no").first()
    return current.rules if current else chama.draft_rules


def _open_invite(code: str, user) -> Invite:
    inv = Invite.objects.select_related("chama").filter(code=code.upper()).first()
    if inv is None or inv.state != Invite.State.PENDING:
        raise Problem("invite_invalid", "This invite is not valid.", status.HTTP_404_NOT_FOUND)
    if inv.expires_at <= timezone.now():
        raise Problem("invite_expired", "This invite has expired. Ask an official for a "
                                        "new one.", status.HTTP_410_GONE)
    if inv.phone_hash and inv.phone_hash != user.phone_hash:
        raise Problem("invite_other_phone", "This invite was sent to a different phone number.",
                      status.HTTP_403_FORBIDDEN)
    return inv


def _join_hash(inv: Invite) -> str:
    return _content_hash({"invite": str(inv.id), "chama": str(inv.chama_id),
                          "role": inv.role, "rules": rules_hash(_rules_for_joining(inv.chama))})


def start_accept(code: str, user) -> SigningChallenge:
    inv = _open_invite(code, user)
    if Membership.objects.filter(chama=inv.chama, user=user).exclude(
            state=Membership.State.EXITED).exists():
        raise Problem("already_member", "You are already a member of this chama.",
                      status.HTTP_409_CONFLICT)
    action = (f"JOIN {inv.chama.name} and accept its rules" if user.language != "sw"
              else f"KUJIUNGA na {inv.chama.name} na kukubali kanuni zake")
    return signing.issue(user=user, purpose=SigningChallenge.Purpose.JOIN_CHAMA,
                         subject=f"chamas.invite:{inv.id}", content_hash=_join_hash(inv),
                         action_text=action)


def confirm_accept(code: str, user, *, challenge_id, sms_code: str) -> Membership:
    inv = _open_invite(code, user)
    signing.confirm(user=user, challenge_id=challenge_id, code=sms_code,
                    purpose=SigningChallenge.Purpose.JOIN_CHAMA,
                    subject=f"chamas.invite:{inv.id}", content_hash=_join_hash(inv))
    with transaction.atomic():
        chama = _lock(inv.chama)
        inv = Invite.objects.select_for_update().get(pk=inv.pk)
        if inv.state != Invite.State.PENDING:
            raise Problem("invite_invalid", "This invite is not valid.",
                          status.HTTP_404_NOT_FOUND)
        if inv.role in OFFICIAL_ROLES and Membership.objects.filter(
                chama=chama, role=inv.role, state=Membership.State.ACTIVE).exists():
            raise Problem("role_taken", "Someone already holds this office.",
                          status.HTTP_409_CONFLICT)
        try:
            with transaction.atomic():
                membership = Membership.objects.create(
                    chama=chama, user=user, role=inv.role, member_code=_next_member_code(chama),
                    is_founding=chama.state != Chama.State.ACTIVE,
                    joined_on=_today() if chama.state == Chama.State.ACTIVE else None,
                )
        except IntegrityError as exc:
            raise Problem("already_member", "You are already a member of this chama.",
                          status.HTTP_409_CONFLICT) from exc
        inv.state, inv.membership = Invite.State.ACCEPTED, membership
        inv.save(update_fields=["state", "membership"])
        if chama.state == Chama.State.ACTIVE:
            ledger.append(chama=chama, type="membership", source="invite", source_id=inv.id,
                          member=membership, decision={"role": membership.role,
                                                       "founding": False})
        elif chama.state == Chama.State.AWAITING_OFFICIALS:
            _open_adoption_if_ready(chama)
    return membership


# --- Adoption ---------------------------------------------------------------------------

def _missing_roles(chama: Chama) -> list[str]:
    held = set(Membership.objects.filter(chama=chama, state=Membership.State.ACTIVE)
               .values_list("role", flat=True))
    return [r for r in chama.draft_rules["officials"]["roles"] if r not in held]


def _open_adoption_if_ready(chama: Chama) -> None:
    if not _missing_roles(chama):
        chama.state = Chama.State.AWAITING_ADOPTION
        chama.adoption_opened_at = timezone.now()
        chama.save(update_fields=["state", "adoption_opened_at"])
        Membership.objects.filter(chama=chama, state=Membership.State.ACTIVE).update(
            is_founding=True)


@transaction.atomic
def submit_for_adoption(chama: Chama) -> Chama:
    chama = _lock(chama)
    if chama.state != Chama.State.SETUP:
        raise Problem("not_in_setup", "This chama has already been submitted.",
                      status.HTTP_409_CONFLICT)
    if active_bank_account(chama) is None:
        raise Problem("bank_account_missing", "Add the chama's bank account first.",
                      status.HTTP_409_CONFLICT)
    validation.validate(chama.draft_rules)
    chama.state = Chama.State.AWAITING_OFFICIALS
    chama.save(update_fields=["state"])
    _open_adoption_if_ready(chama)
    return chama


def _adopt_hash(chama: Chama) -> str:
    return _content_hash({"chama": str(chama.id), "rules": rules_hash(chama.draft_rules)})


def start_adoption(chama: Chama, user) -> SigningChallenge:
    if chama.state != Chama.State.AWAITING_ADOPTION:
        raise Problem("not_awaiting_adoption", "The rules are not open for confirmation.",
                      status.HTTP_409_CONFLICT)
    action = (f"CONFIRM the rule book of {chama.name}" if user.language != "sw"
              else f"KUTHIBITISHA kanuni za {chama.name}")
    return signing.issue(user=user, purpose=SigningChallenge.Purpose.ADOPT_RULES,
                         subject=f"chamas.chama:{chama.id}", content_hash=_adopt_hash(chama),
                         action_text=action)


def confirm_adoption(chama: Chama, membership: Membership, *, challenge_id,
                     sms_code: str) -> Chama:
    signing.confirm(user=membership.user, challenge_id=challenge_id, code=sms_code,
                    purpose=SigningChallenge.Purpose.ADOPT_RULES,
                    subject=f"chamas.chama:{chama.id}", content_hash=_adopt_hash(chama))
    with transaction.atomic():
        chama = _lock(chama)
        if chama.state != Chama.State.AWAITING_ADOPTION:
            raise Problem("not_awaiting_adoption", "The rules are not open for confirmation.",
                          status.HTTP_409_CONFLICT)
        Membership.objects.filter(pk=membership.pk, rules_adopted_at__isnull=True).update(
            rules_adopted_at=timezone.now())
        if adoption_reached(chama):
            _activate(chama)
    return chama


def adoption_status(chama: Chama) -> dict:
    founding = list(Membership.objects.filter(chama=chama, is_founding=True,
                                              state=Membership.State.ACTIVE))
    adopted = [m for m in founding if m.rules_adopted_at]
    officials_waiting = [m.member_code for m in founding
                         if m.is_official and not m.rules_adopted_at]
    pct = chama.draft_rules.get("adoption", {}).get("members_share_pct", 51)
    needed = -(-pct * len(founding) // 100)  # ceiling
    return {"founding": len(founding), "confirmed": len(adopted), "needed": needed,
            "officials_waiting": officials_waiting}


def adoption_reached(chama: Chama) -> bool:
    s = adoption_status(chama)
    return s["founding"] > 0 and not s["officials_waiting"] and s["confirmed"] >= s["needed"]


def _activate(chama: Chama) -> None:
    """Called with the chama locked. Freezes the rules into version 1 and writes the
    opening facts to the ledger."""
    today = _today()
    rules = validation.validate(chama.draft_rules)
    version = RuleVersion.objects.create(
        chama=chama, version_no=1, rules=rules, schema_version=rules["schema_version"],
        template=chama.draft_template, effective_from=today, hash=rules_hash(rules),
        state=RuleVersion.State.IN_FORCE,
    )
    ledger.append(chama=chama, type="rule_version_adopted", source="vote", source_id=version.id,
                  rule_version_id=version.id, rule_refs=["7.1"],
                  decision={"version_no": 1, "rules_hash": version.hash,
                            "effective_from": today.isoformat()})
    founding = Membership.objects.filter(chama=chama, is_founding=True,
                                         state=Membership.State.ACTIVE).order_by("member_code")
    for m in founding:
        ledger.append(chama=chama, type="membership", source="vote", source_id=version.id,
                      member=m, rule_version_id=version.id,
                      decision={"role": m.role, "founding": True})
    founding.update(joined_on=today)
    chama.state = Chama.State.ACTIVE
    chama.activated_at = timezone.now()
    chama.save(update_fields=["state", "activated_at"])


def expire_stale_setups(now=None) -> int:
    now = now or timezone.now()
    return Chama.objects.filter(
        state__in=[Chama.State.SETUP, Chama.State.AWAITING_OFFICIALS,
                   Chama.State.AWAITING_ADOPTION],
        setup_expires_at__lte=now,
    ).update(state=Chama.State.EXPIRED)
