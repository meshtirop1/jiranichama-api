"""Year-end share-out (2026-10-21): figures from the ledger, a members' vote on exactly
those figures, fines and loans settled from the share, payments through money out, and
savings given back never paid twice."""

import re
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.chamas.models import Membership
from apps.contributions.projections import position
from apps.core.problems import Problem
from apps.fines.models import Fine
from apps.ledger import services as ledger
from apps.ledger.models import LedgerEntry
from apps.payouts import services as payouts
from apps.reconciliation import daily
from apps.reconciliation import services as recon
from apps.shareout import services as shareout
from apps.shareout.models import ShareOut
from apps.votes import services as votes

from .phase1 import active_chama, credit, make_user
from .test_phase2_money_out import _statement

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()
START = TODAY - timedelta(days=60)


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


def _vote_all(so, members, choice, outbox):
    v = so.vote
    for m in members:
        ch = votes.start_sms_ballot(v, m, choice)
        v = votes.cast_sms(v, m, choice=choice, challenge_id=ch.id, sms_code=_code(outbox))
    so.refresh_from_db()
    return v


@pytest.fixture
def chama(settings, sms_outbox):
    """Three officials and three members. Each member saved KES 2,000 (the officials
    KES 1,000), the bank paid KES 900 interest, and member 6 owes a KES 200 fine."""
    settings.BANK_STATEMENT_SENDERS = {"equity": ["equitybank.example"]}
    c, (chair, treasurer, secretary) = active_chama(since=START)
    members = [chair, treasurer, secretary]
    for i, name in enumerate(["Mary", "John", "Akinyi"], start=4):
        members.append(Membership.objects.create(
            chama=c, user=make_user(f"+25479900090{i}", name), joined_on=START,
            member_code=f"{c.member_code_prefix}-{i:03d}", is_founding=True))
    for m in members:
        credit(c, amount_cents=200_000 if m.role == "member" else 100_000,
               value_date=START, narration=m.member_code)
    for txn in c.bank_transactions.filter(state="unmatched"):
        recon.match_credit(txn)
    ledger.append(chama=c, type="interest_earned", source="test", amount_cents=90_000,
                  decision={"value_date": (START + timedelta(days=30)).isoformat()})
    raised = ledger.append(chama=c, type="fine_raised", source="test", member=members[5],
                           amount_cents=20_000, period=f"{START.year}-{START.month:02d}")
    Fine.objects.create(chama=c, membership=members[5], rule_ref="3.2", amount_cents=20_000,
                        period=raised.period, raised_entry=raised)
    _statement(c, [(TODAY - timedelta(days=1), "DEPOSITS", 0, 2_000_000)], 0)
    for official in (chair, treasurer):
        ch = payouts.start_enable(c, official)
        payouts.enable(c, official, challenge_id=ch.id, sms_code=_code(sms_outbox))
    c.refresh_from_db()
    return c, members


def _draft(c, members, **kw):
    return shareout.draft(c, members[0], period_from=START, period_to=TODAY,
                          **{"weight": "savings", **kw})


def test_the_figures_come_from_the_ledger(chama):
    c, members = chama
    so = _draft(c, members)
    f = so.figures
    assert f["earned"]["total_cents"] == 90_000
    lines = {x["member_code"]: x for x in f["lines"]}
    mary = lines[members[3].member_code]
    chair = lines[members[0].member_code]
    # Shared by savings: 9,000 saved in all; Mary saved 2,000 of it.
    assert mary["profit_cents"] == 90_000 * 200_000 // 900_000 == 20_000
    assert chair["profit_cents"] == 10_000
    assert mary["savings_cents"] == 200_000 and mary["payout_cents"] == 220_000
    akinyi = lines[members[5].member_code]
    assert akinyi["fines_cents"] == 20_000 and akinyi["payout_cents"] == 200_000
    assert f["payout_cents"] == sum(x["payout_cents"] for x in f["lines"])
    assert f["reserve_cents"] == 0


def test_keeping_a_reserve_and_keeping_savings(chama):
    c, members = chama
    so = _draft(c, members, keep_pct=20, return_savings=False)
    f = so.figures
    assert f["shared_cents"] == 72_000 and f["reserve_cents"] == 18_000
    assert all(x["savings_cents"] == 0 for x in f["lines"])


def test_one_open_share_out_and_only_officials_prepare_it(chama):
    c, members = chama
    with pytest.raises(Problem):
        shareout.draft(c, members[3], period_from=START, period_to=TODAY)
    _draft(c, members)
    with pytest.raises(Problem) as e:
        _draft(c, members)
    assert e.value.problem_code == "shareout_open"


def test_the_members_vote_then_each_payment_goes_through_money_out(
        chama, sms_outbox, django_capture_on_commit_callbacks):
    c, members = chama
    so = _draft(c, members)
    so = shareout.submit(so, members[0])
    assert so.state == "voting" and so.vote.eligible == 6
    assert so.figures_hash[:8] in so.vote.question
    with django_capture_on_commit_callbacks(execute=True):
        _vote_all(so, members, "yes", sms_outbox)
    assert so.state == "paying"
    assert so.entry.type == "shareout_approved"
    assert so.entry.decision["figures_hash"] == so.figures_hash
    # Akinyi's fine was paid from her share, in the books: the bank check leaves it out.
    fine = Fine.objects.get(membership=members[5])
    assert fine.state == "paid"
    assert LedgerEntry.objects.get(type="fine_paid", source="shareout").amount_cents == 20_000
    assert "shareout" in daily.INSIDE_BOOKS
    lines = {x.membership_id: x for x in so.lines.select_related("proposal")}
    assert all(x.proposal is not None for x in lines.values())
    # No second members' vote, and an official's own share is proposed by that official.
    assert not any(x.proposal.needs_vote for x in lines.values())
    assert lines[members[1].id].proposal.proposed_by_id == members[1].id
    mary = lines[members[3].id].proposal
    assert mary.amount_cents == 220_000 and mary.proposed_by_id == members[0].id

    # Paying Mary: the officials approve, the bank pays, the debit is matched.
    for official in (members[1], members[2]):
        ch = payouts.start_approve(mary, official)
        payouts.approve(mary, official, challenge_id=ch.id, sms_code=_code(sms_outbox))
    payouts.mark_paid(mary, members[1], "SHR1")
    _statement(c, [(TODAY, "PESALINK SHR1 MARY", 220_000, 0)], 2_000_000)
    mary.refresh_from_db()
    assert mary.state == "paid"
    assert mary.entry.decision["savings_out_cents"] == 200_000
    # Her savings went back to her: they are not her savings any more.
    assert position(members[3], TODAY).contributed_cents == 0
    assert position(members[4], TODAY).contributed_cents == 200_000
    so.refresh_from_db()
    assert so.state == "paying"  # the others are not paid yet


def test_a_failed_vote_changes_nothing(chama, sms_outbox, django_capture_on_commit_callbacks):
    c, members = chama
    so = shareout.submit(_draft(c, members), members[0])
    with django_capture_on_commit_callbacks(execute=True):
        _vote_all(so, members, "no", sms_outbox)
    assert so.state == "rejected" and not so.lines.exists()
    assert Fine.objects.get(membership=members[5]).state == "raised"


def test_share_out_through_the_api(chama, api):
    c, members = chama
    api.force_authenticate(members[0].user)
    r = api.post(f"/api/v1/chamas/{c.id}/shareouts/",
                 {"period_from": START.isoformat(), "period_to": TODAY.isoformat(),
                  "weight": "savings"}, format="json")
    assert r.status_code == 201 and r.data["state"] == "draft" and r.data["can_manage"]
    sid = r.data["id"]
    api.force_authenticate(members[3].user)
    r = api.get(f"/api/v1/shareouts/{sid}/")
    assert [x for x in r.data["lines"] if x["mine"]][0]["payout_cents"] == 220_000
    assert api.post(f"/api/v1/shareouts/{sid}/submit/").status_code == 403
    api.force_authenticate(members[0].user)
    assert api.post(f"/api/v1/shareouts/{sid}/cancel/").data["state"] == "cancelled"
    assert ShareOut.objects.get(pk=sid).state == "cancelled"
