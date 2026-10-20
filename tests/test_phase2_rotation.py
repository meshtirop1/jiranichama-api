"""The merry-go-round rotation: the recorded draw, whose turn is next, payouts filled in,
and members behind on contributions (rules 8.1, 8.2)."""

import base64
import re
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.chamas.models import Membership
from apps.contributions.projections import position
from apps.core.problems import Problem
from apps.feeds import services as feeds
from apps.ledger.hashing import GENESIS_PREV_HASH
from apps.ledger.models import ChainHead, LedgerEntry
from apps.ledger.services import verify_chain
from apps.payouts import rotation as rot
from apps.payouts import services
from apps.payouts.models import RotationTurn
from apps.reconciliation import services as recon
from apps.rules.templates import template_for

from .phase1 import active_chama, credit, idem, make_user, statement_csv

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


def _statement(chama, lines, opening_cents):
    data = statement_csv(lines, opening_cents=opening_cents)
    [s] = feeds.receive_mail({
        "to": chama.statement_inbox.address, "from": "statements@equitybank.example",
        "auth": {"spf": "pass", "dkim": "pass", "dmarc": "pass",
                 "dkim_domain": "equitybank.example"},
        "attachments": [{"filename": "s.csv", "content_b64": base64.b64encode(data).decode()}],
    })
    assert s.state == "accepted", s.problems
    return s


def _setup(settings, outbox, *, rotation=None, since=TODAY):
    """Four members, money out on, KES 100,000 reconciled."""
    settings.BANK_STATEMENT_SENDERS = {"equity": ["equitybank.example"]}
    rules = template_for("merry_go_round")
    if rotation:
        rules["rotation"] = rotation
    c, (chair, treasurer, secretary) = active_chama(since=since, rules=rules)
    member = Membership.objects.create(chama=c, user=make_user("+254799000555", "Mary"),
                                       joined_on=since, is_founding=True,
                                       member_code=f"{c.member_code_prefix}-004")
    _statement(c, [(TODAY - timedelta(days=1), "DEPOSIT", 0, 10_000_000)], 0)
    for o in (chair, treasurer):
        ch = services.start_enable(c, o)
        services.enable(c, o, challenge_id=ch.id, sms_code=_code(outbox))
    c.refresh_from_db()
    return c, chair, treasurer, secretary, member


def _approve_and_pay(p, officials, outbox, chama, opening):
    for o in officials:
        ch = services.start_approve(p, o)
        services.approve(p, o, challenge_id=ch.id, sms_code=_code(outbox))
    services.mark_paid(p, officials[0], "")
    _statement(chama, [(TODAY, f"TRANSFER {p.id.hex[:6]}", p.amount_cents, 0)], opening)
    p.refresh_from_db()
    assert p.state == "paid"


def test_the_draw_is_recorded_and_anyone_can_re_run_it(settings, sms_outbox):
    c, chair, *_ = _setup(settings, sms_outbox)
    head = ChainHead.objects.filter(chama=c).first()
    seed_before = head.hash if head else GENESIS_PREV_HASH
    r = rot.start(c, chair)
    codes = sorted(Membership.objects.filter(chama=c).values_list("member_code", flat=True))
    expected = rot.draw_order(seed_before, codes)
    assert r.method == "draw" and r.seed == seed_before
    assert [t.membership.member_code for t in r.turns.order_by("position")] == expected
    assert r.entry.type == "rotation_set" and r.entry.decision["order"] == expected
    with pytest.raises(Problem) as exc:
        rot.start(c, chair)
    assert exc.value.problem_code == "rotation_running"
    assert verify_chain(c, check_signatures=False).ok


def test_a_fixed_order_needs_every_member_once(settings, sms_outbox):
    c, chair, treasurer, secretary, member = _setup(
        settings, sms_outbox, rotation={"order": "fixed", "arrears": "skip"})
    with pytest.raises(Problem) as exc:
        rot.start(c, chair, order_ids=[member.id, chair.id])
    assert exc.value.problem_code == "order_incomplete"
    r = rot.start(c, chair, order_ids=[member.id, secretary.id, treasurer.id, chair.id])
    assert [t.membership_id for t in r.turns.order_by("position")] == [
        member.id, secretary.id, treasurer.id, chair.id]


def test_next_payout_is_filled_in_and_the_turn_moves_on_when_paid(settings, sms_outbox):
    c, chair, treasurer, secretary, member = _setup(
        settings, sms_outbox, rotation={"order": "fixed", "arrears": "skip"})
    rot.start(c, chair, order_ids=[member.id, secretary.id, treasurer.id, chair.id])
    p = rot.propose_next(c, secretary)
    assert p.payee_membership_id == member.id
    assert p.amount_cents == 400_000  # KES 1,000 x 4 members
    assert p.purpose == "Merry-go-round turn 1 of 4, cycle 1"
    with pytest.raises(Problem) as exc:
        rot.propose_next(c, secretary)
    assert exc.value.problem_code == "turn_in_progress"

    _approve_and_pay(p, [chair, treasurer], sms_outbox, c, 10_000_000)
    turn = RotationTurn.objects.get(proposal=p)
    assert turn.state == "paid"
    q = rot.propose_next(c, secretary)
    assert q.payee_membership_id == secretary.id
    assert q.purpose == "Merry-go-round turn 2 of 4, cycle 1"


def test_a_declined_turn_can_be_proposed_again(settings, sms_outbox):
    c, chair, treasurer, secretary, member = _setup(
        settings, sms_outbox, rotation={"order": "fixed", "arrears": "skip"})
    rot.start(c, chair, order_ids=[member.id, secretary.id, treasurer.id, chair.id])
    p = rot.propose_next(c, secretary)
    # The secretary proposed, so only the chair and treasurer approve: one "no" ends it.
    services.decline(p, treasurer, "Wait for the meeting")
    q = rot.propose_next(c, secretary)
    assert q.id != p.id and q.payee_membership_id == member.id


def _behind_chama(settings, outbox, arrears):
    """Started 70 days ago; everyone but Mary has paid up."""
    c, chair, treasurer, secretary, member = _setup(
        settings, outbox, rotation={"order": "fixed", "arrears": arrears},
        since=TODAY - timedelta(days=70))
    for m in (chair, treasurer, secretary):
        recon.match_credit(credit(c, amount_cents=1_000_000, value_date=TODAY,
                                  narration=f"MPESA {m.member_code}"))
        assert position(m, TODAY).arrears_cents == 0
    assert position(member, TODAY).arrears_cents > 0
    rot.start(c, chair, order_ids=[member.id, secretary.id, treasurer.id, chair.id])
    return c, chair, treasurer, secretary, member


def test_skip_moves_a_member_behind_to_the_end(settings, sms_outbox):
    c, chair, _, secretary, member = _behind_chama(settings, sms_outbox, "skip")
    p = rot.propose_next(c, secretary)
    assert p.payee_membership_id == secretary.id
    turns = list(rot.latest(c).turns.order_by("position"))
    assert (turns[0].membership_id, turns[0].state) == (member.id, "skipped")
    assert "Behind on contributions" in turns[0].note
    assert (turns[-1].membership_id, turns[-1].position, turns[-1].state) == (
        member.id, 5, "pending")
    assert p.purpose == "Merry-go-round turn 1 of 4, cycle 1"


def test_block_waits_for_the_member_to_catch_up(settings, sms_outbox):
    c, chair, treasurer, secretary, member = _behind_chama(settings, sms_outbox, "block")
    with pytest.raises(Problem) as exc:
        rot.propose_next(c, secretary)
    assert exc.value.problem_code == "turn_blocked"


def test_deduct_pays_the_pot_less_arrears_as_two_entries(settings, sms_outbox):
    c, chair, treasurer, secretary, member = _behind_chama(settings, sms_outbox, "deduct")
    owed = position(member, TODAY).arrears_cents
    p = rot.propose_next(c, secretary)
    assert p.payee_membership_id == member.id
    assert (p.amount_cents, p.deduct_cents) == (400_000 - owed, owed)
    _approve_and_pay(p, [chair, treasurer], sms_outbox, c, 10_000_000)
    payout = LedgerEntry.objects.get(pk=p.entry_id)
    assert payout.amount_cents == -400_000  # the whole pot, never a silent smaller payout
    assert payout.decision["deducted_for_arrears"] == owed
    contributions = LedgerEntry.objects.filter(member=member, type="contribution",
                                               source_id=p.matched_txn_id)
    assert sum(e.amount_cents for e in contributions) == owed
    assert position(member, TODAY).arrears_cents == 0


def test_rotation_through_the_api(api, settings, sms_outbox):
    c, chair, treasurer, secretary, member = _setup(settings, sms_outbox)
    api.force_authenticate(member.user)
    r = api.get(f"/api/v1/chamas/{c.id}/rotation/").json()
    assert r["settings"] == {"order": "draw", "arrears": "skip"} and r["running"] is False

    api.force_authenticate(chair.user)
    r = api.post(f"/api/v1/chamas/{c.id}/rotation/", {}, format="json", **idem())
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["running"] and body["next_position"] == 1 and body["pot_cents"] == 400_000
    assert len(body["turns"]) == 4 and body["receipt"]
    first = body["turns"][0]["membership_id"]

    r = api.post(f"/api/v1/chamas/{c.id}/rotation/next/", {}, format="json", **idem())
    if first in (str(chair.id), str(treasurer.id), str(secretary.id)):
        assert r.status_code == 201  # an official's turn: the other officials approve
    assert r.status_code == 201, r.content
    assert r.json()["purpose"].startswith("Merry-go-round turn 1 of 4")
    r = api.get(f"/api/v1/chamas/{c.id}/rotation/").json()
    assert r["turns"][0]["proposal_state"] == "collecting_approvals"
