"""The monthly bill (owner decisions 2026-10-20)."""

from datetime import date, timedelta
from unittest import mock

import pytest
from django.utils import timezone

from apps.billing import services as billing
from apps.billing.models import Bill
from apps.chamas.models import Chama
from apps.ledger import services as ledger

from .phase1 import active_chama

pytestmark = pytest.mark.django_db
TODAY = timezone.localdate()


def _active_in(chama, members, period):
    """Give each member one record dated in the month."""
    start, _ = billing.month_bounds(period)
    when = timezone.make_aware(timezone.datetime(start.year, start.month, 10, 12))
    with mock.patch("django.utils.timezone.now", return_value=when):
        for m in members:
            ledger.append(chama=chama, type="membership", source="test", member=m,
                          decision={"change": "joined"})


def _old(chama, months=6):
    Chama.objects.filter(pk=chama.pk).update(
        activated_at=timezone.now() - timedelta(days=31 * months))
    chama.refresh_from_db()


def test_active_members_are_billed_at_the_local_price():
    c, ms = active_chama(since=TODAY - timedelta(days=200), members=8)
    _old(c)
    period = billing.previous_period()
    _active_in(c, ms[:7], period)  # one member did nothing: free
    b = billing.issue(c, period)
    assert b.active_members == 7 and b.price_per_member_cents == 1_500
    assert b.total_cents == 7 * 1_500 and b.state == Bill.State.OPEN
    assert billing.issue(c, period).id == b.id  # once per month


def test_the_first_three_months_and_small_chamas_are_free():
    c, ms = active_chama(since=TODAY, members=8)
    period = billing.previous_period()
    _active_in(c, ms, period)
    b = billing.issue(c, period)
    assert b.free_reason == "trial" and b.total_cents == 0 and b.state == "free"

    small, few = active_chama(since=TODAY - timedelta(days=200), members=5)
    _old(small)
    _active_in(small, few, period)
    b = billing.issue(small, period)
    assert b.free_reason == "small" and b.total_cents == 0


def test_prices_in_other_currencies():
    assert billing.price_per_member_cents("UGX") == 40_000
    assert billing.price_per_member_cents("TZS") == 30_000
    assert billing.price_per_member_cents("RWF") == 16_000  # KES 15 x 11, rounded


def test_members_see_this_month_and_past_bills(api):
    c, ms = active_chama(since=TODAY - timedelta(days=200), members=6)
    _old(c)
    period = billing.previous_period()
    _active_in(c, ms, period)
    billing.issue(c, period)
    api.force_authenticate(ms[-1].user)
    body = api.get(f"/api/v1/chamas/{c.id}/bills/").json()
    assert body["bills"][0]["period"] == period and body["bills"][0]["total_cents"] == 9_000
    assert body["pricing"]["free_months"] == 3 and "this_month" in body


def test_the_month_boundaries():
    assert billing.month_bounds("2026-12") == (date(2026, 12, 1), date(2026, 12, 31))
    assert billing.previous_period(date(2026, 1, 5)) == "2025-12"
