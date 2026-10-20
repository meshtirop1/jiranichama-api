"""The merry-go-round rotation (5.6 step 9, rules 8.1 and 8.2). See docs/flows/rotation.md.

A cycle gives every member one turn. The order is fixed by the officials or drawn: the
draw's seed is the chama's chain fingerprint at that moment, and the order sorts member
codes by SHA-256("{seed}:{code}"). Seed and order go on the ledger, so anyone can
re-run the draw and get the same order.
"""

import hashlib

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework import status

from apps.chamas.models import Chama, Membership
from apps.contributions.projections import position
from apps.core.problems import Problem
from apps.ledger import services as ledger
from apps.ledger.hashing import GENESIS_PREV_HASH
from apps.ledger.models import ChainHead
from apps.rules.models import version_at

from . import services
from .models import Proposal, Rotation, RotationTurn

# A turn's proposal that ended without payment frees the turn for a new proposal.
FAILED = (Proposal.State.REFUSED, Proposal.State.REJECTED, Proposal.State.EXPIRED,
          Proposal.State.CANCELLED)


def settings_for(chama: Chama) -> dict | None:
    version = version_at(chama, timezone.localdate())
    return version.rules.get("rotation") if version else None


def draw_order(seed: str, member_codes: list[str]) -> list[str]:
    """The published draw: anyone holding the seed and the codes gets the same order."""
    return sorted(member_codes,
                  key=lambda code: hashlib.sha256(f"{seed}:{code}".encode()).hexdigest())


def current(chama: Chama) -> Rotation | None:
    """The latest cycle, while any of its turns is still pending."""
    r = Rotation.objects.filter(chama=chama).order_by("-cycle_no").first()
    if r and r.turns.filter(state=RotationTurn.State.PENDING).exists():
        return r
    return None


def latest(chama: Chama) -> Rotation | None:
    return Rotation.objects.filter(chama=chama).order_by("-cycle_no").first()


def start(chama: Chama, by: Membership, *, order_ids: list | None = None) -> Rotation:
    """Start a cycle, by the method the rules set. ``order_ids`` (membership ids) is the
    officials' order for a fixed rotation."""
    services._official(by)
    if chama.state != Chama.State.ACTIVE:
        raise Problem("chama_not_active", "This is done once the chama is active.",
                      status.HTTP_409_CONFLICT)
    rules = settings_for(chama)
    if rules is None:
        raise Problem("no_rotation", "The chama's rules have no rotation.",
                      status.HTTP_409_CONFLICT)
    members = list(Membership.objects.filter(chama=chama, state=Membership.State.ACTIVE)
                   .order_by("member_code"))
    by_id = {str(m.id): m for m in members}
    with transaction.atomic():
        Chama.objects.select_for_update().get(pk=chama.pk)
        if current(chama) is not None:
            raise Problem("rotation_running", "A rotation is still running.",
                          status.HTTP_409_CONFLICT)
        if rules["order"] == Rotation.Method.FIXED:
            ids = [str(i) for i in (order_ids or [])]
            if sorted(ids) != sorted(by_id):
                raise Problem("order_incomplete", "Put every member in the order, once each.",
                              status.HTTP_400_BAD_REQUEST)
            ordered, seed = [by_id[i] for i in ids], ""
        else:
            head = ChainHead.objects.filter(chama=chama).first()
            seed = head.hash if head else GENESIS_PREV_HASH
            codes = draw_order(seed, [m.member_code for m in members])
            by_code = {m.member_code: m for m in members}
            ordered = [by_code[c] for c in codes]
        cycle = (Rotation.objects.filter(chama=chama).aggregate(n=Max("cycle_no"))["n"] or 0) + 1
        entry = ledger.append(
            chama=chama, type="rotation_set", source="rotation",
            decision={"cycle": cycle, "method": rules["order"], "seed": seed,
                      "order": [m.member_code for m in ordered],
                      "started_by": by.member_code})
        rotation = Rotation.objects.create(chama=chama, cycle_no=cycle, method=rules["order"],
                                           seed=seed, entry=entry, started_by=by)
        RotationTurn.objects.bulk_create([
            RotationTurn(rotation=rotation, position=i, membership=m)
            for i, m in enumerate(ordered, start=1)])
    return rotation


def pot_cents(rotation: Rotation) -> int:
    """What one turn receives: every member's contribution under the rules in force."""
    version = version_at(rotation.chama, timezone.localdate())
    members = rotation.turns.values("membership_id").distinct().count()
    return version.rules["contributions"]["amount_cents"] * members


def _free(turn: RotationTurn) -> bool:
    return turn.state == RotationTurn.State.PENDING and (
        turn.proposal_id is None or turn.proposal.state in FAILED)


def next_turn(rotation: Rotation) -> RotationTurn | None:
    for t in rotation.turns.select_related("proposal", "membership").order_by("position"):
        if t.state == RotationTurn.State.PENDING:
            return t
    return None


def propose_next(chama: Chama, by: Membership) -> Proposal:
    """Fill in the next turn's payout (5.6 step 1): the member whose turn it is and the
    pot, with arrears handled as rule 8.2 says."""
    services._money_out(chama)
    services._official(by)
    rules = settings_for(chama)
    rotation = current(chama)
    if rules is None or rotation is None:
        raise Problem("no_rotation_running", "Start the rotation first.",
                      status.HTTP_409_CONFLICT)
    today = timezone.localdate()
    with transaction.atomic():
        Chama.objects.select_for_update().get(pk=chama.pk)
        skipped_now = set()
        while True:
            turn = next_turn(rotation)
            if turn is None:
                raise Problem("rotation_complete", "Everyone has had their turn.",
                              status.HTTP_409_CONFLICT)
            if not _free(turn):
                raise Problem("turn_in_progress", "The current turn already has a payout "
                                                  "proposal.", status.HTTP_409_CONFLICT,
                              proposal_id=str(turn.proposal_id))
            member = turn.membership
            behind = position(member, today).arrears_cents
            if behind <= 0:
                break
            if rules["arrears"] == "skip" and member.id not in skipped_now:
                skipped_now.add(member.id)
                turn.state = RotationTurn.State.SKIPPED
                turn.note = (f"Behind on contributions ({services._kes(behind, chama)}); "
                             "moved to the end.")
                turn.save(update_fields=["state", "note"])
                last = rotation.turns.aggregate(n=Max("position"))["n"]
                RotationTurn.objects.create(rotation=rotation, position=last + 1,
                                            membership=member)
                continue
            if rules["arrears"] == "deduct":
                break
            raise Problem("turn_blocked", f"{member.member_code} has "
                                          f"{services._kes(behind, chama)} unpaid and must "
                                          "catch up "
                                          "first (rule 8.2).", status.HTTP_409_CONFLICT,
                          member_code=member.member_code)

        pot = pot_cents(rotation)
        deduct = behind if behind > 0 and rules["arrears"] == "deduct" else 0
        if deduct >= pot:
            raise Problem("turn_blocked", f"{member.member_code} owes more than the pot.",
                          status.HTTP_409_CONFLICT, member_code=member.member_code)
        n_turns = rotation.turns.exclude(state=RotationTurn.State.SKIPPED).count()
        done = rotation.turns.filter(state=RotationTurn.State.PAID).count()
        purpose = f"Merry-go-round turn {done + 1} of {n_turns}, cycle {rotation.cycle_no}"
        if deduct:
            purpose += f" (less {services._kes(deduct, chama)} owed)"
        p = services.propose(chama, by, kind=Proposal.Kind.PAYOUT, amount_cents=pot - deduct,
                             purpose=purpose, payee=member, allow_arrears=bool(deduct),
                             deduct_cents=deduct)
        turn.proposal = p
        turn.save(update_fields=["proposal"])
    return p


def on_paid(p: Proposal) -> None:
    """The turn whose payout this was is done."""
    RotationTurn.objects.filter(proposal=p, state=RotationTurn.State.PENDING).update(
        state=RotationTurn.State.PAID)
