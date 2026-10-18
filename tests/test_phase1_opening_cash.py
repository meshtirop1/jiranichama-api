"""Opening balances and cash at meetings."""

import re
from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.contributions import services
from apps.contributions.models import CashReceipt, OpeningBalance
from apps.contributions.projections import position
from apps.core.problems import Problem
from apps.fines.models import Fine
from apps.ledger.models import LedgerEntry
from apps.ledger.services import verify_chain
from apps.rules.templates import template_for

from .phase1 import active_chama, credit, idem

pytestmark = pytest.mark.django_db


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


def _confirm_opening(ob, by, outbox):
    ch = services.start_confirm_opening(ob, by)
    return services.confirm_opening(ob, by, challenge_id=ch.id, sms_code=_code(outbox))


def _confirm_cash(receipt, member, outbox):
    ch = services.start_confirm_cash(receipt, member)
    return services.confirm_cash(receipt, member, challenge_id=ch.id, sms_code=_code(outbox))


# --- Opening balances -------------------------------------------------------------------

def test_opening_balances_drafted_confirmed_and_on_the_ledger(sms_outbox):
    chama, (chair, treasurer, secretary) = active_chama(since=timezone.localdate())
    ob = services.save_opening(chama, treasurer, [
        {"membership_id": chair.id, "savings_cents": 1_250_000, "fines_cents": 0},
        {"membership_id": secretary.id, "savings_cents": 800_000, "fines_cents": 40_000},
    ])
    assert ob.state == "draft" and not LedgerEntry.objects.filter(type="opening_balance").exists()

    with pytest.raises(Problem) as exc:
        services.start_confirm_opening(ob, treasurer)
    assert exc.value.problem_code == "second_official_required"

    ob = _confirm_opening(ob, chair, sms_outbox)
    assert ob.state == "confirmed" and ob.objection_until > timezone.now()
    entries = LedgerEntry.objects.filter(chama=chama, source="proposal").order_by("seq")
    assert [(e.type, e.member_code, e.amount_cents) for e in entries] == [
        ("opening_balance", chair.member_code, 1_250_000),
        ("opening_balance", secretary.member_code, 800_000),
        ("fine_raised", secretary.member_code, 40_000),
    ]
    assert entries[0].decision["signed_by"] == [treasurer.member_code, chair.member_code]
    p = position(secretary, timezone.localdate())
    assert (p.contributed_cents, p.opening_cents, p.fines_cents) == (800_000, 800_000, 40_000)

    with pytest.raises(Problem):
        services.save_opening(chama, treasurer, [])
    assert verify_chain(chama, check_signatures=False).ok


def test_opening_lines_are_checked():
    chama, (chair, treasurer, _) = active_chama(since=timezone.localdate())
    other, (other_chair, _, _) = active_chama(since=timezone.localdate())
    for bad in ([{"membership_id": other_chair.id, "savings_cents": 1}],
                [{"membership_id": chair.id, "savings_cents": -5}],
                [{"membership_id": chair.id, "savings_cents": 1.5}],
                [{"membership_id": chair.id}, {"membership_id": chair.id}]):
        with pytest.raises(Problem):
            services.save_opening(chama, treasurer, bad)
    with pytest.raises(Problem) as exc:
        services.save_opening(chama, chair, [{"membership_id": chair.id}])
    assert exc.value.problem_code == "not_allowed"


def test_members_object_only_to_their_own_line_and_in_time(sms_outbox):
    chama, (chair, treasurer, secretary) = active_chama(since=timezone.localdate())
    ob = services.save_opening(chama, treasurer, [
        {"membership_id": secretary.id, "savings_cents": 800_000, "fines_cents": 0}])
    ob = _confirm_opening(ob, chair, sms_outbox)
    line = ob.lines.get()
    with pytest.raises(Problem):
        services.object_to_opening(line, chair, "not mine to say")
    services.object_to_opening(line, secretary, "I had KES 9,000, not 8,000")
    with pytest.raises(Problem) as exc:
        services.object_to_opening(line, secretary, "again")
    assert exc.value.problem_code == "already_objected"
    OpeningBalance.objects.filter(pk=ob.pk).update(
        objection_until=timezone.now() - timedelta(seconds=1))
    line.refresh_from_db()
    with pytest.raises(Problem) as exc:
        services.object_to_opening(line, secretary, "late")
    assert exc.value.problem_code == "objection_closed"


def test_opening_fines_are_paid_first_and_never_reversed(sms_outbox):
    chama, (chair, treasurer, secretary) = active_chama(since=date(2026, 1, 1))
    ob = services.save_opening(chama, treasurer, [
        {"membership_id": secretary.id, "savings_cents": 0, "fines_cents": 30_000}])
    _confirm_opening(ob, chair, sms_outbox)
    from apps.reconciliation.services import match_credit

    txn = credit(chama, amount_cents=30_000, value_date=timezone.localdate(),
                 narration=f"{secretary.member_code}")
    # Order arrears -> fines -> current: arrears exist since January, so pay them first.
    match_credit(txn)
    fine = Fine.objects.get(membership=secretary, rule_ref="opening")
    assert fine.state == "raised"  # the money went to January's arrears, by the rules

    rules = template_for("merry_go_round")
    rules["contributions"]["allocation_order"] = ["fines", "arrears", "current"]
    chama2, (chair2, treasurer2, secretary2) = active_chama(since=timezone.localdate(),
                                                            rules=rules)
    ob2 = services.save_opening(chama2, treasurer2, [
        {"membership_id": secretary2.id, "savings_cents": 0, "fines_cents": 30_000}])
    _confirm_opening(ob2, chair2, sms_outbox)
    txn2 = credit(chama2, amount_cents=30_000, value_date=timezone.localdate(),
                  narration=f"{secretary2.member_code}")
    match_credit(txn2)
    fine2 = Fine.objects.get(membership=secretary2, rule_ref="opening")
    assert fine2.state == "paid"
    assert not LedgerEntry.objects.filter(chama=chama2, type="reversal").exists()


# --- Cash at meetings -------------------------------------------------------------------

def test_cash_recorded_confirmed_by_member_then_banked_in_time(sms_outbox):
    start = date(2026, 3, 1)
    chama, (chair, treasurer, secretary) = active_chama(since=start)
    r1 = services.record_cash(chama, treasurer, chair, amount_cents=100_000,
                              received_on=date(2026, 3, 4))
    r2 = services.record_cash(chama, treasurer, secretary, amount_cents=100_000,
                              received_on=date(2026, 3, 4))
    assert r1.state == "recorded" and not LedgerEntry.objects.filter(
        type="contribution_pending_cash").exists()

    with pytest.raises(Problem) as exc:
        services.start_confirm_cash(r1, secretary)
    assert exc.value.problem_code == "not_yours"
    r1 = _confirm_cash(r1, chair, sms_outbox)
    r2 = _confirm_cash(r2, secretary, sms_outbox)
    assert r1.state == "member_confirmed"
    assert position(chair, date(2026, 3, 5)).pending_cash_cents == 100_000

    # Banked on the 6th: within the 3 days, so it counts from the 4th (on time).
    deposit = credit(chama, amount_cents=200_000, value_date=date(2026, 3, 6),
                     narration="CASH DEPOSIT")
    with pytest.raises(Problem) as exc:
        services.bank_cash(deposit, treasurer, [r1.id])
    assert exc.value.problem_code == "amounts_differ"
    services.bank_cash(deposit, treasurer, [r1.id, r2.id])

    for r in (r1, r2):
        r.refresh_from_db()
        assert r.state == "banked"
    contrib = LedgerEntry.objects.get(chama=chama, type="contribution", member=chair)
    assert (contrib.period, contrib.decision["outcome"]) == ("2026-03", "on_time")
    assert contrib.decision["banked_in_time"] is True
    assert LedgerEntry.objects.filter(chama=chama, type="cash_banked").count() == 2
    assert position(chair, date(2026, 3, 9)).pending_cash_cents == 0
    deposit.refresh_from_db()
    assert deposit.state == "recorded"


def test_cash_banked_late_counts_from_the_bank_date(sms_outbox):
    chama, (chair, treasurer, _) = active_chama(since=date(2026, 3, 1))
    r = services.record_cash(chama, treasurer, chair, amount_cents=100_000,
                             received_on=date(2026, 3, 4))
    r = _confirm_cash(r, chair, sms_outbox)
    assert services.is_overdue(r, today=date(2026, 3, 8))
    deposit = credit(chama, amount_cents=100_000, value_date=date(2026, 3, 10))
    services.bank_cash(deposit, treasurer, [r.id])
    contrib = LedgerEntry.objects.get(chama=chama, type="contribution", member=chair)
    assert contrib.decision["outcome"] == "late"
    assert contrib.decision["banked_in_time"] is False


def test_member_can_decline_cash_they_did_not_give(sms_outbox):
    chama, (chair, treasurer, _) = active_chama(since=date(2026, 3, 1))
    r = services.record_cash(chama, treasurer, chair, amount_cents=50_000,
                             received_on=date(2026, 3, 4))
    r = services.decline_cash(r, chair, "I paid nothing that day")
    assert r.state == "declined"
    deposit = credit(chama, amount_cents=50_000, value_date=date(2026, 3, 5))
    with pytest.raises(Problem) as exc:
        services.bank_cash(deposit, treasurer, [r.id])
    assert exc.value.problem_code == "invalid_receipts"


def test_only_the_treasurer_records_and_banks_cash():
    chama, (chair, treasurer, _) = active_chama(since=date(2026, 3, 1))
    with pytest.raises(Problem):
        services.record_cash(chama, chair, chair, amount_cents=1, received_on=date(2026, 3, 4))
    with pytest.raises(Problem):
        services.record_cash(chama, treasurer, chair, amount_cents=1,
                             received_on=timezone.localdate() + timedelta(days=1))


def test_cash_api_round_trip(api, sms_outbox):
    chama, (chair, treasurer, _) = active_chama(since=date(2026, 3, 1))
    api.force_authenticate(treasurer.user)
    r = api.post(f"/api/v1/chamas/{chama.id}/cash-receipts/",
                 {"membership_id": str(chair.id), "amount_cents": 100_000,
                  "received_on": "2026-03-04"}, format="json", **idem())
    assert r.status_code == 201, r.content
    rid = r.json()["id"]
    api.force_authenticate(chair.user)
    r = api.post(f"/api/v1/cash-receipts/{rid}/confirm/", {}, format="json")
    assert r.status_code == 202
    r = api.post(f"/api/v1/cash-receipts/{rid}/confirm/",
                 {"challenge_id": r.json()["challenge_id"], "code": _code(sms_outbox)},
                 format="json")
    assert r.json()["state"] == "member_confirmed"
    pos = api.get(f"/api/v1/memberships/{chair.id}/position/").json()
    assert pos["pending_cash_cents"] == 100_000
    deposit = credit(chama, amount_cents=100_000, value_date=date(2026, 3, 5))
    api.force_authenticate(treasurer.user)
    r = api.post(f"/api/v1/bank-transactions/{deposit.id}/bank-cash/", {"receipt_ids": [rid]},
                 format="json", **idem())
    assert r.status_code == 200, r.content
    assert CashReceipt.objects.get(pk=rid).state == "banked"


def test_opening_api_round_trip(api, sms_outbox):
    chama, (chair, treasurer, secretary) = active_chama(since=timezone.localdate())
    api.force_authenticate(treasurer.user)
    r = api.put(f"/api/v1/chamas/{chama.id}/opening-balances/", {"lines": [
        {"membership_id": str(secretary.id), "savings_cents": 500_000, "fines_cents": 0}]},
        format="json")
    assert r.status_code == 200 and r.json()["state"] == "draft"
    api.force_authenticate(chair.user)
    r = api.post(f"/api/v1/chamas/{chama.id}/opening-balances/confirm/", {}, format="json")
    r = api.post(f"/api/v1/chamas/{chama.id}/opening-balances/confirm/",
                 {"challenge_id": r.json()["challenge_id"], "code": _code(sms_outbox)},
                 format="json")
    assert r.json()["state"] == "confirmed"
    line_id = r.json()["lines"][0]["id"]
    api.force_authenticate(secretary.user)
    r = api.post(f"/api/v1/opening-balance-lines/{line_id}/object/",
                 {"reason": "It was more"}, format="json")
    assert r.status_code == 201
    assert api.get(f"/api/v1/chamas/{chama.id}/opening-balances/").json()["lines"][0][
        "objections"] == ["It was more"]
