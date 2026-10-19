"""Leaving the chama and the death of a member (5.13)."""

import base64
import copy
import re
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.chamas.access import require_member
from apps.chamas.models import Membership
from apps.core.problems import Problem
from apps.exits import services as exits
from apps.feeds import services as feeds
from apps.fines.models import Fine
from apps.ledger import services as ledger
from apps.ledger.models import LedgerEntry
from apps.payouts import services as payouts
from apps.reconciliation.daily import records_cents
from apps.rules.templates import template_for

from .phase1 import active_chama, statement_csv

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


@pytest.fixture
def setup(settings, sms_outbox):
    settings.BANK_STATEMENT_SENDERS = {"equity": ["equitybank.example"]}

    def make(**exit_rules):
        rules = copy.deepcopy(template_for("merry_go_round"))
        rules["exit"].update(exit_rules)
        c, ms = active_chama(since=TODAY, members=5, rules=rules)
        _statement(c, [(TODAY - timedelta(days=5), "DEPOSIT", 0, 10_000_000)], 0)
        for o in ms[:2]:
            ch = payouts.start_enable(c, o)
            payouts.enable(c, o, challenge_id=ch.id, sms_code=_code(sms_outbox))
        c.refresh_from_db()
        mary = ms[3]
        ledger.append(chama=c, type="opening_balance", source="test", member=mary,
                      amount_cents=2_000_000)
        return c, ms
    return make


def _leave(m, outbox):
    ch = exits.start_leave(m)
    assert "LEAVE" in outbox[-1][1] and "get back" in outbox[-1][1]
    return exits.leave(m, challenge_id=ch.id, sms_code=_code(outbox))


def _approve(p, by, outbox):
    ch = payouts.start_approve(p, by)
    return payouts.approve(p, by, challenge_id=ch.id, sms_code=_code(outbox))


def test_the_quote_shows_what_comes_back(setup):
    c, (chair, treasurer, secretary, mary, john) = setup(deduction_pct=10)
    q = exits.quote(mary)
    assert q["savings_cents"] == 2_000_000 and q["deduction_cents"] == 200_000
    assert q["settlement_cents"] == 1_800_000 and q["notice_days"] == 30


def test_notice_keeps_the_member_reading_and_they_can_change_their_mind(setup, sms_outbox):
    c, (chair, treasurer, secretary, mary, john) = setup()
    with pytest.raises(Problem) as e:
        exits.start_leave(chair)
    assert e.value.problem_code == "official_must_hand_over"
    e = _leave(mary, sms_outbox)
    mary.refresh_from_db()
    assert e.state == "notice" and mary.state == "exiting"
    assert e.exit_on == TODAY + timedelta(days=30)
    assert require_member(mary.user, c).id == mary.id  # still sees the chama
    exits.cancel(e, mary)
    mary.refresh_from_db()
    assert mary.state == "active"


def test_settled_from_savings_paid_out_then_exited(setup, sms_outbox):
    c, (chair, treasurer, secretary, mary, john) = setup(notice_days=0)
    entry = ledger.append(chama=c, type="fine_raised", source="test", member=mary,
                          amount_cents=20_000, period="2026-01", rule_refs=["3.2"])
    Fine.objects.create(chama=c, membership=mary, rule_ref="3.2", period="2026-01",
                        amount_cents=20_000, raised_entry=entry)
    before = records_cents(c, 0)
    e = _leave(mary, sms_outbox)
    assert e.state == "settling"
    assert Fine.objects.get(membership=mary).state == "paid"
    assert records_cents(c, 0) == before  # paid from savings: no bank money moved
    p = e.proposal
    assert p.kind == "settlement" and p.amount_cents == 1_980_000
    assert p.payee_membership_id == mary.id
    _approve(p, chair, sms_outbox)
    _approve(p, treasurer, sms_outbox)
    payouts.mark_paid(p, treasurer, "PL9")
    _statement(c, [(TODAY - timedelta(days=1), "PESALINK PL9 MARY", 1_980_000, 0)],
               10_000_000)
    e.refresh_from_db()
    mary.refresh_from_db()
    assert e.state == "settled" and mary.state == "exited"
    assert LedgerEntry.objects.filter(member=mary, type="membership",
                                      decision__change="exited").exists()


def test_a_death_is_recorded_and_the_next_of_kin_is_paid(setup, sms_outbox):
    c, (chair, treasurer, secretary, mary, john) = setup(death_benefit_cents=500_000)
    doc = SimpleUploadedFile("permit.jpg", b"\xff\xd8 permit", content_type="image/jpeg")
    with pytest.raises(Problem):
        exits.record_death(c, john, mary, died_on=TODAY, next_of_kin_name="Paul",
                           next_of_kin_phone="0712000000", document=doc)
    e = exits.record_death(c, secretary, mary, died_on=TODAY, next_of_kin_name="Paul Wanjiru",
                           next_of_kin_phone="0712000000", document=doc)
    mary.refresh_from_db()
    assert mary.state == "deceased" and e.document_sha256
    assert e.proposal.payee_name == "Paul Wanjiru" and e.proposal.amount_cents == 2_000_000
    assert e.benefit_proposal.amount_cents == 500_000
    assert e.entry.decision["change"] == "deceased"


def test_exits_through_the_api(api, setup, sms_outbox):
    c, (chair, treasurer, secretary, mary, john) = setup()
    api.force_authenticate(mary.user)
    assert api.get(f"/api/v1/chamas/{c.id}/exit-quote/").json()["settlement_cents"] == 2_000_000
    r = api.post(f"/api/v1/chamas/{c.id}/leave/", {}, format="json")
    assert r.status_code == 202
    r = api.post(f"/api/v1/chamas/{c.id}/leave/",
                 {"challenge_id": r.json()["challenge_id"], "sms_code": _code(sms_outbox)},
                 format="json")
    assert r.status_code == 201 and r.json()["state"] == "notice"
    api.force_authenticate(secretary.user)
    listed = api.get(f"/api/v1/chamas/{c.id}/exits/").json()
    assert listed[0]["member"] and listed[0]["mine"] is False

    doc = SimpleUploadedFile("permit.jpg", b"\xff\xd8 permit", content_type="image/jpeg")
    r = api.post(f"/api/v1/memberships/{john.id}/death/",
                 {"died_on": TODAY.isoformat(), "next_of_kin_name": "Ann",
                  "next_of_kin_phone": "0711000000", "document": doc}, format="multipart")
    assert r.status_code == 201, r.content
    assert Membership.objects.get(pk=john.pk).state == "deceased"
