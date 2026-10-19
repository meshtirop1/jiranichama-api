"""Nightly public anchoring of every chama's chain head (6.6)."""

import json

import pytest
from django.core import mail
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from apps.ledger import anchoring
from apps.ledger import services as ledger
from apps.ledger.models import ChainHead

from .phase1 import active_chama

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


@pytest.fixture(autouse=True)
def no_network(settings, monkeypatch):
    settings.OTS_CALENDARS = ["https://calendar.example"]
    settings.ANCHOR_EMAIL_TO = ["owner@outside.example"]

    class R:
        status_code = 200
        content = b"\x00ots-pending-proof"

    sent = []

    def post(url, data, headers, timeout):
        sent.append((url, data))
        return R()

    monkeypatch.setattr(anchoring.requests, "post", post)
    return sent


def _chamas(n):
    out = []
    for _ in range(n):
        c, (chair, *_) = active_chama(since=TODAY)
        ledger.append(chama=c, type="membership", source="test", member=chair)
        out.append(c)
    return out


def test_merkle_paths_prove_each_leaf():
    leaves = [anchoring.leaf_hash(f"c{i}", i, "ab" * 32) for i in range(5)]
    root = anchoring.merkle_root(leaves)
    for i, leaf in enumerate(leaves):
        assert anchoring.verify_path(leaf, anchoring.merkle_path(leaves, i), root)
    assert not anchoring.verify_path(leaves[0], anchoring.merkle_path(leaves, 1), root)


def test_one_root_for_every_chain_timestamped_and_emailed(no_network):
    chamas = _chamas(3)
    a = anchoring.anchor_now()
    assert len(a.leaves) == ChainHead.objects.filter(seq__gt=0).count() >= 3
    assert a.ots_status == "submitted" and bytes(a.ots_proof) == b"\x00ots-pending-proof"
    url, data = no_network[0]
    assert url == "https://calendar.example/digest" and data == bytes.fromhex(a.merkle_root)
    assert a.emailed and mail.outbox[-1].to == ["owner@outside.example"]
    attached = json.loads(mail.outbox[-1].attachments[0][1])
    assert attached["merkle_root"] == a.merkle_root

    proof = anchoring.proof_for(a, chamas[1].id)
    head = ChainHead.objects.get(chama=chamas[1])
    assert proof["seq"] == head.seq and proof["hash"] == head.hash
    assert anchoring.verify_path(proof["leaf"], proof["path"], a.merkle_root)


def test_a_rewritten_head_no_longer_fits_the_root():
    c, = _chamas(1)
    a = anchoring.anchor_now()
    proof = anchoring.proof_for(a, c.id)
    forged = anchoring.leaf_hash(str(c.id), proof["seq"], "00" * 32)
    assert not anchoring.verify_path(forged, proof["path"], a.merkle_root)


def test_an_anchor_cannot_be_edited():
    _chamas(1)
    a = anchoring.anchor_now()
    with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cur:
        cur.execute("UPDATE ledger_anchor SET merkle_root = %s WHERE id = %s", ["0" * 64, a.id])


def test_members_get_their_chamas_proofs(api):
    c, = _chamas(1)
    anchoring.anchor_now()
    chair = c.memberships.get(role="chair")
    api.force_authenticate(chair.user)
    [p] = api.get(f"/api/v1/chamas/{c.id}/anchors/").json()
    assert anchoring.verify_path(p["leaf"], p["path"], p["merkle_root"])
