"""Public anchoring (Phase 2, 6.6): every night, one fingerprint for every chama's chain.

At 00:30 the latest entry (seq and hash) of every chama's chain becomes a leaf; the
leaves, sorted by chama, are hashed into a Merkle root. The root is:

- sent to the public OpenTimestamps calendars, whose proof later lands in Bitcoin, so
  anyone can show the root existed that night;
- emailed, with every leaf, to a mailbox outside our systems (``ANCHOR_EMAIL_TO``);
- kept as an ``Anchor`` (insert only, apart from the timestamp fields).

Each chama can get its leaf and Merkle path (``proof_for``); its members' phones check
that their chain head is in that night's root. Neither we nor anyone with our database
can later rewrite a chama's history without the root changing.

Leaf: SHA-256 of canonical JSON {"chama_id", "seq", "hash"}. Node: SHA-256 of the two
child hashes' bytes, left then right; an odd last node is paired with itself.
"""

import hashlib
import json

import requests
from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

from apps.core.canonical import canonical_bytes

from .models import Anchor, ChainHead


def leaf_hash(chama_id: str, seq: int, head_hash: str) -> str:
    return hashlib.sha256(canonical_bytes(
        {"chama_id": chama_id, "seq": seq, "hash": head_hash})).hexdigest()


def _parent(a: str, b: str) -> str:
    return hashlib.sha256(bytes.fromhex(a) + bytes.fromhex(b)).hexdigest()


def merkle_root(leaves: list[str]) -> str:
    if not leaves:
        return hashlib.sha256(b"").hexdigest()
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [_parent(level[i], level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def merkle_path(leaves: list[str], index: int) -> list[dict]:
    """Sibling hashes from the leaf up, each with the side it sits on."""
    path, level, i = [], list(leaves), index
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        sibling = i ^ 1
        path.append({"side": "left" if sibling < i else "right", "hash": level[sibling]})
        level = [_parent(level[k], level[k + 1]) for k in range(0, len(level), 2)]
        i //= 2
    return path


def verify_path(leaf: str, path: list[dict], root: str) -> bool:
    h = leaf
    for step in path:
        h = _parent(step["hash"], h) if step["side"] == "left" else _parent(h, step["hash"])
    return h == root


def _submit_ots(root: str) -> tuple[bytes, str]:
    """Post the root to the first OpenTimestamps calendar that answers; returns the
    calendar's pending proof (it upgrades to a Bitcoin attestation within hours)."""
    for url in settings.OTS_CALENDARS:
        try:
            r = requests.post(f"{url.rstrip('/')}/digest", data=bytes.fromhex(root),
                              headers={"Accept": "application/vnd.opentimestamps.v1",
                                       "User-Agent": "jiranichama-anchor"}, timeout=20)
            if r.status_code == 200 and r.content:
                return r.content, url
        except requests.RequestException:
            continue
    return b"", ""


def anchor_now() -> Anchor:
    heads = list(ChainHead.objects.filter(seq__gt=0).order_by("chama_id")
                 .values_list("chama_id", "seq", "hash"))
    leaves = [{"chama_id": str(c), "seq": s, "hash": h} for c, s, h in heads]
    root = merkle_root([leaf_hash(x["chama_id"], x["seq"], x["hash"]) for x in leaves])
    with transaction.atomic():
        anchor = Anchor.objects.create(merkle_root=root, leaves=leaves)
    if settings.OTS_CALENDARS:
        proof, calendar = _submit_ots(root)
        Anchor.objects.filter(pk=anchor.pk).update(
            ots_proof=proof or None, ots_calendar=calendar,
            ots_status=Anchor.OtsStatus.SUBMITTED if proof else Anchor.OtsStatus.FAILED)
    if settings.ANCHOR_EMAIL_TO:
        body = (f"JiraniChama anchor {anchor.at.isoformat()}\nMerkle root: {root}\n"
                f"Chains: {len(leaves)}\n")
        msg = EmailMessage(subject=f"JiraniChama anchor {timezone.localdate().isoformat()}",
                           body=body, to=list(settings.ANCHOR_EMAIL_TO))
        msg.attach(f"anchor-{anchor.id}.json",
                   json.dumps({"merkle_root": root, "leaves": leaves}, indent=1),
                   "application/json")
        sent = msg.send(fail_silently=True)
        Anchor.objects.filter(pk=anchor.pk).update(emailed=bool(sent))
    anchor.refresh_from_db()
    # Elections tied twice are settled by a lot drawn from this root (elections.draw_lots).
    from apps.elections.services import draw_lots

    draw_lots(anchor)
    return anchor


def proof_for(anchor: Anchor, chama_id) -> dict | None:
    ids = [x["chama_id"] for x in anchor.leaves]
    try:
        i = ids.index(str(chama_id))
    except ValueError:
        return None
    hashes = [leaf_hash(x["chama_id"], x["seq"], x["hash"]) for x in anchor.leaves]
    leaf = anchor.leaves[i]
    return {"anchor_id": str(anchor.id), "at": anchor.at.isoformat(),
            "merkle_root": anchor.merkle_root, "seq": leaf["seq"], "hash": leaf["hash"],
            "leaf": hashes[i], "path": merkle_path(hashes, i),
            "ots_status": anchor.ots_status, "ots_calendar": anchor.ots_calendar}
