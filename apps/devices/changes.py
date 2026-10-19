"""Changing phones (5.12). See docs/flows/device-keys.md, "Changing phones".

- With the old phone: it signs the hand-over to the new key, and the new key is active at
  once.
- Without it (lost, broken): the new phone signs in with the phone SMS code and registers;
  the old key is revoked at once. An official's new key then waits for every other
  official in each of their chamas to vouch for it (a members' vote where fewer than two
  other officials exist). Anyone else's key waits 24 hours. Until then they can read but
  not sign, by key or by SMS code: a SIM swap gets the code, not the signature.
"""

import base64
import hashlib
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import status

from apps.core.canonical import canonical_bytes
from apps.core.problems import Problem

from . import attestation, signing
from .models import Device, DeviceApproval, DeviceNonce, SigningChallenge

COOLING = timedelta(hours=24)


def _officials_memberships(user):
    from apps.chamas.models import OFFICIAL_ROLES, Membership

    return Membership.objects.filter(user=user, role__in=OFFICIAL_ROLES,
                                     state=Membership.State.ACTIVE).select_related("chama")


def handover_payload(user, old: Device, new_fingerprint: str, nonce: str) -> dict:
    return {"v": 1, "purpose": "handover", "user": str(user.id),
            "old_fingerprint": old.fingerprint, "new_fingerprint": new_fingerprint,
            "nonce": nonce}


def start_handover(user, old: Device, new_public_key_der: bytes) -> dict:
    """On the old phone: what it must sign to hand over to the new key."""
    from .keys import issue_nonce

    if old.user_id != user.id or old.state != Device.State.ACTIVE:
        raise Problem("device_invalid", "Hand over from your active phone.",
                      status.HTTP_403_FORBIDDEN)
    n = issue_nonce(user, DeviceNonce.Use.HANDOVER, f"devices.device:{old.id}")
    payload = handover_payload(user, old, attestation.spki_fingerprint(new_public_key_der),
                               n.nonce)
    return {"device_id": str(old.id), "payload": payload,
            "payload_hash": hashlib.sha256(canonical_bytes(payload)).hexdigest()}


def check_handover(user, der: bytes, handover: dict) -> Device:
    """The old phone's signature over the hand-over, checked; returns the old device."""
    from .keys import take_nonce

    old = Device.objects.filter(pk=handover.get("device_id"), user=user,
                                state=Device.State.ACTIVE).first()
    if old is None:
        raise Problem("device_invalid", "The old phone's key is not active.",
                      status.HTTP_403_FORBIDDEN)
    take_nonce(user, DeviceNonce.Use.HANDOVER, handover.get("nonce", ""),
               f"devices.device:{old.id}")
    message = canonical_bytes(handover_payload(user, old, attestation.spki_fingerprint(der),
                                               handover["nonce"]))
    try:
        signature = base64.b64decode(handover.get("signature", ""))
    except (ValueError, TypeError):
        signature = b""
    if not attestation.verify_signature(bytes(old.public_key_der), message, signature):
        raise Problem("signature_invalid", "The old phone did not sign this hand-over.",
                      status.HTTP_403_FORBIDDEN)
    return old


def arrival_for(user, handover_old: Device | None) -> dict:
    """How a new key starts: fields for Device.objects.create."""
    previous = Device.objects.filter(user=user).order_by("-registered_at").first()
    if handover_old is not None:
        return {"arrival": "handover", "replaces": handover_old, "state": Device.State.ACTIVE}
    if previous is None:
        return {"arrival": "first", "state": Device.State.ACTIVE}
    official = _officials_memberships(user).exists()
    return {"arrival": "lost", "replaces": previous, "state": Device.State.PENDING,
            "needs_officials": official,
            "active_from": None if official else timezone.now() + COOLING}


def after_create(device: Device) -> None:
    """Revoke what it replaces, and for an official's new phone, ask for vouching."""
    now = timezone.now()
    Device.objects.filter(user=device.user, state__in=[Device.State.ACTIVE,
                                                       Device.State.PENDING]) \
        .exclude(pk=device.pk).update(state=Device.State.REVOKED, revoked_at=now)
    if device.state == Device.State.PENDING and device.needs_officials:
        for m in _officials_memberships(device.user):
            if len(_other_officials(m)) < 2:
                _open_vote(device, m)
            else:
                transaction.on_commit(lambda m=m: _tell_officials(device.pk, m.pk))


def _other_officials(m):
    from apps.chamas.models import OFFICIAL_ROLES, Membership

    return list(Membership.objects.filter(chama=m.chama, role__in=OFFICIAL_ROLES,
                                          state=Membership.State.ACTIVE)
                .exclude(pk=m.pk).select_related("user"))


def _name(m) -> str:
    return m.user.display_name or m.member_code


def _tell_officials(device_id, membership_id) -> None:
    from apps.chamas.models import Membership
    from apps.notify.sms import SmsFailed, send_sms

    m = Membership.objects.select_related("chama", "user").get(pk=membership_id)
    for o in _other_officials(m):
        text = {"en": f"{m.chama.name}: {_name(m)} set up a new phone after losing the old "
                      "one. Check it is really them, then vouch for it in the app.",
                "sw": f"{m.chama.name}: {_name(m)} ameweka simu mpya baada ya kupoteza ya "
                      "zamani. Hakikisha ni yeye kweli, kisha mthibitishe kwenye programu."}
        try:
            send_sms(o.user.phone, text.get(o.user.language, text["en"]))
        except SmsFailed:
            continue


def _open_vote(device: Device, m) -> None:
    from apps.rules.models import version_at
    from apps.votes import services as votes

    rules = version_at(m.chama, timezone.localdate()).rules
    votes.open_vote(m.chama, subject=f"device:{device.id}:{m.chama_id}",
                    question=f"Accept {_name(m)}'s new phone for approvals?", rules=rules,
                    closes_at=timezone.now() + timedelta(days=7), exclude=[m.id])


def pending(user) -> Device | None:
    promote_due(user)
    return Device.objects.filter(user=user, state=Device.State.PENDING).first()


def require_can_sign(user) -> None:
    """While a new phone waits, nothing is signed: not by key, not by SMS code."""
    if pending(user) is not None:
        raise Problem("device_pending", "Your new phone is waiting to be confirmed. You can "
                                        "read, but not approve or vote, until then.",
                      status.HTTP_409_CONFLICT)


def promote_due(user=None) -> int:
    qs = Device.objects.filter(state=Device.State.PENDING, needs_officials=False,
                               active_from__lte=timezone.now())
    if user is not None:
        qs = qs.filter(user=user)
    n = 0
    for d in qs:
        _activate(d)
        n += 1
    return n


def _activate(device: Device) -> None:
    from .keys import _announce

    Device.objects.filter(pk=device.pk, state=Device.State.PENDING) \
        .update(state=Device.State.ACTIVE)
    device.refresh_from_db()
    _announce(device)
    _install_elected(device.user)


def _install_elected(user) -> None:
    from apps.elections.models import Election
    from apps.elections.services import try_install

    for e in Election.objects.filter(candidate__user=user, state=Election.State.WON):
        try_install(e)


def _check_vouched(device: Device) -> None:
    """Active once every chama where they are an official has vouched."""
    for m in _officials_memberships(device.user):
        approvals = DeviceApproval.objects.filter(device=device, chama=m.chama)
        if approvals.filter(by_vote=True).exists():
            continue
        others = {o.id for o in _other_officials(m)}
        if len(others) < 2 or not others <= set(approvals.values_list("membership_id",
                                                                       flat=True)):
            return
    _activate(device)


def _hash(device: Device, chama_id) -> str:
    return hashlib.sha256(canonical_bytes({"device": str(device.id), "chama": str(chama_id),
                                           "fingerprint": device.fingerprint})).hexdigest()


def _require_voucher(device: Device, by) -> None:
    if device.state != Device.State.PENDING or not device.needs_officials:
        raise Problem("already_answered", "This phone is not waiting for officials.",
                      status.HTTP_409_CONFLICT)
    owner = _officials_memberships(device.user).filter(chama=by.chama).first()
    if owner is None or by.id not in {o.id for o in _other_officials(owner)}:
        raise Problem("not_allowed", "The other officials of this chama vouch for it.",
                      status.HTTP_403_FORBIDDEN)
    require_can_sign(by.user)
    if DeviceApproval.objects.filter(device=device, chama=by.chama, membership=by).exists():
        raise Problem("already_answered", "You have already vouched.",
                      status.HTTP_409_CONFLICT)


def start_vouch(device: Device, by) -> SigningChallenge:
    _require_voucher(device, by)
    who = device.user.display_name or device.user.phone_hint
    text = (f"VOUCH for {who}'s new phone" if by.user.language != "sw"
            else f"KUTHIBITISHA simu mpya ya {who}")
    return signing.issue(user=by.user, purpose=SigningChallenge.Purpose.APPROVE_DEVICE,
                         subject=f"devices.device:{device.id}",
                         content_hash=_hash(device, by.chama_id), action_text=text)


def vouch(device: Device, by, *, challenge_id, sms_code: str) -> Device:
    _require_voucher(device, by)
    signing.confirm(user=by.user, challenge_id=challenge_id, code=sms_code,
                    purpose=SigningChallenge.Purpose.APPROVE_DEVICE,
                    subject=f"devices.device:{device.id}",
                    content_hash=_hash(device, by.chama_id))
    with transaction.atomic():
        device = Device.objects.select_for_update().get(pk=device.pk)
        DeviceApproval.objects.create(device=device, chama=by.chama, membership=by,
                                      challenge_id=challenge_id)
        _check_vouched(device)
    device.refresh_from_db()
    return device


def on_vote_closed(vote) -> None:
    _, device_id, chama_id = vote.subject.split(":")
    device = Device.objects.select_for_update().get(pk=device_id)
    if device.state != Device.State.PENDING:
        return
    if vote.state == "passed":
        DeviceApproval.objects.create(device=device, chama_id=chama_id, by_vote=True)
        _check_vouched(device)
    else:
        device.state, device.revoked_at = Device.State.REVOKED, timezone.now()
        device.save(update_fields=["state", "revoked_at"])


def waiting_in(chama) -> list[Device]:
    """New phones of this chama's officials still waiting to be vouched for."""
    from apps.chamas.models import OFFICIAL_ROLES, Membership

    users = Membership.objects.filter(chama=chama, role__in=OFFICIAL_ROLES,
                                      state=Membership.State.ACTIVE).values("user_id")
    return list(Device.objects.filter(user_id__in=users, state=Device.State.PENDING,
                                      needs_officials=True).select_related("user"))
