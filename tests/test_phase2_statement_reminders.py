"""Overdue bank statements: the treasurer, then the officials, then every member (5.5)."""

import base64
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.chamas.models import Membership
from apps.feeds import services as feeds
from apps.reconciliation import freshness
from apps.reconciliation.models import StatementReminder

from .phase1 import active_chama, make_user, statement_csv

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


@pytest.fixture(autouse=True)
def mail(settings):
    settings.BANK_STATEMENT_SENDERS = {"equity": ["equitybank.example"]}


def _statement(chama, day, name="s.csv", opening=0):
    data = statement_csv([(day, "DEPOSIT", 0, 1_000)], opening_cents=opening)
    [s] = feeds.receive_mail({
        "to": chama.statement_inbox.address, "from": "s@equitybank.example",
        "auth": {"spf": "pass", "dkim": "pass", "dmarc": "pass",
                 "dkim_domain": "equitybank.example"},
        "attachments": [{"filename": name, "content_b64": base64.b64encode(data).decode()}]})
    assert s.state == "accepted", s.problems


def _chama():
    c, (chair, treasurer, secretary) = active_chama(since=TODAY)  # statements every 14 days
    Membership.objects.create(chama=c, user=make_user("+254799000901", "Mary"),
                              joined_on=TODAY, member_code=f"{c.member_code_prefix}-004")
    _statement(c, TODAY)
    return c


def test_fresh_until_the_rules_period_ends():
    c = _chama()
    s = freshness.status(c, TODAY + timedelta(days=14))
    assert s["level"] == 0 and s["due_on"] == (TODAY + timedelta(days=14)).isoformat()
    assert s["checked_to"] == TODAY.isoformat() and s["every_days"] == 14


@pytest.mark.parametrize("days_after_due,level", [(1, 1), (6, 1), (7, 2), (13, 2), (14, 3)])
def test_levels_rise_with_days_overdue(days_after_due, level):
    c = _chama()
    s = freshness.status(c, TODAY + timedelta(days=14 + days_after_due))
    assert s["level"] == level and s["days_overdue"] == days_after_due


def test_the_treasurer_then_the_officials_are_reminded_once_each(sms_outbox):
    c = _chama()
    before = len(sms_outbox)
    assert freshness.remind(c, TODAY + timedelta(days=15)) == 1  # the treasurer
    assert "overdue" in sms_outbox[-1][1]
    assert freshness.remind(c, TODAY + timedelta(days=16)) == 0  # not every day
    assert freshness.remind(c, TODAY + timedelta(days=21)) == 3  # all three officials
    assert freshness.remind(c, TODAY + timedelta(days=29)) == 0  # members are not texted
    assert len(sms_outbox) == before + 4
    assert StatementReminder.objects.filter(chama=c).count() == 2


def test_a_new_statement_ends_the_spell_and_the_next_one_reminds_again(sms_outbox):
    base = TODAY - timedelta(days=40)  # everything in the past: statements cannot be future
    c, _ = active_chama(since=base)
    _statement(c, base)
    assert freshness.remind(c, base + timedelta(days=15)) == 1
    _statement(c, base + timedelta(days=15), "t.csv", opening=1_000)
    assert freshness.status(c, base + timedelta(days=16))["level"] == 0
    # Overdue again a period later: a new spell, reminded again.
    assert freshness.remind(c, base + timedelta(days=31)) == 1


def test_before_any_statement_it_counts_from_going_live():
    c, _ = active_chama(since=TODAY)
    s = freshness.status(c, TODAY + timedelta(days=20))
    assert s["checked_to"] is None and s["level"] == 1


def test_the_home_screen_summary_carries_it(api):
    c = _chama()
    chair = Membership.objects.get(chama=c, role="chair")
    api.force_authenticate(chair.user)
    body = api.get(f"/api/v1/chamas/{c.id}/reconciliation/").json()
    assert body["statement"]["level"] == 0 and body["statement"]["every_days"] == 14
