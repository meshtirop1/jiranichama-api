"""Who a phone should trust, and how members vouch for the records (protection plan
2026-10-20, "remember the cryptographic part").

1. **The platform key chain.** The app is built knowing the fingerprint of the first
   platform key. Every later key is listed with an endorsement: the previous key's
   signature over the new key. A phone walks the chain from the key it was built with
   and accepts a record's signature only from a key on that chain. Someone who steals the
   server cannot slip in their own key: they would need the old private key (in KMS) to
   endorse it.

2. **Member countersignatures.** After a phone has checked a chama's whole chain, the
   member can sign "this chain, up to entry N, has hash H" with the key in their phone's
   secure chip. Every phone checks every countersignature against its own copy of the
   chain. Once members have signed a point in the history, replacing it would need every
   one of those members' phones.
"""

import base64
import hashlib

from django.db import IntegrityError, transaction
from rest_framework import status

from apps.core.canonical import canonical_bytes
from apps.core.problems import Problem

from .models import HeadCountersignature, LedgerEntry, PlatformKey
from .signing import _PREHASHED, get_signer, public_key_pem


def _fingerprint_pem(pem: str) -> str:
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_public_key(pem.encode())
    der = key.public_bytes(serialization.Encoding.DER,
                           serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()


def endorsement_payload(*, key_id: str, fingerprint: str, prev_key_id: str) -> dict:
    """What the old key signs. The fingerprint (SHA-256 of the new key's DER) binds the
    exact key; the phone recomputes it from the PEM it is given."""
    return {"v": 1, "purpose": "platform_key_endorsement", "key_id": key_id,
            "fingerprint": fingerprint, "prev_key_id": prev_key_id}


def ensure_current_key() -> PlatformKey:
    """The signing key in use, on the chain. The very first key needs no endorsement; any
    other key that is not on the chain yet is refused, never added silently."""
    signer = get_signer()
    pem = public_key_pem(signer.public_key())
    k = PlatformKey.objects.filter(key_id=signer.key_id).first()
    if k:
        return k
    if PlatformKey.objects.exists():
        raise RuntimeError("The signing key is not endorsed by the previous key. Run "
                           "`manage.py endorse_platform_key` with the old key first.")
    try:
        return PlatformKey.objects.create(key_id=signer.key_id, public_key_pem=pem,
                                          fingerprint=_fingerprint_pem(pem))
    except IntegrityError:  # another worker added it first
        return PlatformKey.objects.get(key_id=signer.key_id)


def endorse(*, new_key_id: str, new_public_key_pem: str) -> PlatformKey:
    """Run while the OLD key is still the signer: it signs the new key, and the new key
    joins the chain. Then switch the server to the new key."""
    old = ensure_current_key()
    fp = _fingerprint_pem(new_public_key_pem)
    payload = endorsement_payload(key_id=new_key_id, fingerprint=fp, prev_key_id=old.key_id)
    digest = hashlib.sha256(canonical_bytes(payload)).digest()
    sig = get_signer().sign_digest(digest)
    return PlatformKey.objects.create(key_id=new_key_id, public_key_pem=new_public_key_pem,
                                      fingerprint=fp, prev_key_id=old.key_id,
                                      endorsement_sig=sig)


def verify_chain(keys: list[PlatformKey]) -> bool:
    """The server's own check of what phones check: each key endorsed by the one before."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization

    for prev, k in zip(keys, keys[1:], strict=False):
        if k.prev_key_id != prev.key_id or not k.endorsement_sig:
            return False
        if _fingerprint_pem(k.public_key_pem) != k.fingerprint:
            return False
        payload = endorsement_payload(key_id=k.key_id, fingerprint=k.fingerprint,
                                      prev_key_id=prev.key_id)
        digest = hashlib.sha256(canonical_bytes(payload)).digest()
        pub = serialization.load_pem_public_key(prev.public_key_pem.encode())
        try:
            pub.verify(bytes(k.endorsement_sig), digest, _PREHASHED)
        except InvalidSignature:
            return False
    return True


def key_chain() -> list[dict]:
    ensure_current_key()
    return [{"key_id": k.key_id, "fingerprint": k.fingerprint,
             "public_key_pem": k.public_key_pem, "prev_key_id": k.prev_key_id or None,
             "endorsement_sig": (base64.b64encode(bytes(k.endorsement_sig)).decode()
                                 if k.endorsement_sig else None),
             "created_at": k.created_at.isoformat()}
            for k in PlatformKey.objects.order_by("created_at")]


# --- Member countersignatures -----------------------------------------------------------

def countersign_payload(*, chama_id, member_code: str, seq: int, head_hash: str) -> dict:
    """What a member's phone signs (canonical JSON, UTF-8): the chama, who they are, and
    the point in the chain they checked."""
    return {"v": 1, "purpose": "countersign_chain", "chama_id": str(chama_id),
            "member_code": member_code, "seq": seq, "hash": head_hash}


def countersign(membership, *, seq: int, head_hash: str, signature_b64: str):
    from apps.devices import attestation, keys
    from apps.devices.changes import require_can_sign

    require_can_sign(membership.user)
    device = keys.active_device(membership.user)
    if device is None:
        raise Problem("no_device_key", "Set up this phone's key first.",
                      status.HTTP_409_CONFLICT)
    entry = LedgerEntry.objects.filter(chama=membership.chama, seq=seq).first()
    if entry is None or entry.hash != head_hash:
        # The phone checked a different history from the one here: say so loudly.
        raise Problem("head_mismatch", "This is not the chama's record at that point.",
                      status.HTTP_409_CONFLICT)
    message = canonical_bytes(countersign_payload(
        chama_id=membership.chama_id, member_code=membership.member_code, seq=seq,
        head_hash=head_hash))
    try:
        signature = base64.b64decode(signature_b64)
    except (ValueError, TypeError):
        signature = b""
    if not attestation.verify_signature(bytes(device.public_key_der), message, signature):
        raise Problem("signature_invalid", "The phone's signature did not check out.")
    existing = HeadCountersignature.objects.filter(membership=membership, seq=seq).first()
    if existing:
        return existing
    with transaction.atomic():
        return HeadCountersignature.objects.create(
            chama=membership.chama, membership=membership, device=device, seq=seq,
            hash=head_hash, signature=signature)


def countersignatures(chama) -> list[dict]:
    """Each member's latest countersignature, with the key that made it, so every phone
    can check the signature itself and compare the hash with its own chain."""
    from apps.devices.models import Device

    latest: dict = {}
    for c in (HeadCountersignature.objects.filter(chama=chama)
              .select_related("membership", "device").order_by("seq")):
        latest[c.membership_id] = c
    out = []
    for c in sorted(latest.values(), key=lambda x: x.membership.member_code):
        der = bytes(c.device.public_key_der)
        out.append({"member_code": c.membership.member_code, "seq": c.seq, "hash": c.hash,
                    "signature": base64.b64encode(bytes(c.signature)).decode(),
                    "device_public_key": base64.b64encode(der).decode(),
                    "device_active": c.device.state == Device.State.ACTIVE,
                    "at": c.created_at.isoformat()})
    return out
