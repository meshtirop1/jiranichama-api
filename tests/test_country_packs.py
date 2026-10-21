"""Country rule packs (2026-10-21): the law of the chama's country sits under its rules."""

from datetime import date

import pytest
from django.utils import timezone

from apps.chamas import services as chamas
from apps.chamas.models import Chama, Membership
from apps.core.problems import Problem
from apps.rules import packs, validation
from apps.rules.templates import template_for

from .phase1 import active_chama, make_user

pytestmark = pytest.mark.django_db
TODAY = timezone.localdate()


def test_uganda_allows_only_simple_interest_and_no_growing_loans():
    r = template_for("table_banking", "UGX")
    r["loans"]["interest"] = "reducing"
    r["loans"]["late_penalty_cents"] = 50_000
    codes = {e.code for e in validation.check(r, "UG")}
    assert {"country_simple_interest", "country_no_default_penalty"} <= codes
    assert validation.check(r, "KE") == []  # Kenya has no such rule


def test_a_new_ugandan_chama_starts_lawful():
    r = template_for("table_banking", "UGX", "UG")
    assert validation.check(r, "UG") == []
    assert r["loans"]["interest"] == "flat" and r["loans"]["late_penalty_cents"] == 0


def test_somalia_loans_are_interest_free_and_burundi_loans_short():
    r = template_for("table_banking", "USD", "SO")
    assert r["loans"]["monthly_rate_bp"] == 0
    r["loans"]["monthly_rate_bp"] = 100
    assert "country_no_interest" in {e.code for e in validation.check(r, "SO")}
    assert template_for("table_banking", "BIF", "BI")["loans"]["max_term_months"] <= 3


def test_ethiopia_warns_above_twelve_percent_a_year():
    r = template_for("table_banking", "ETB")
    r["loans"]["monthly_rate_bp"] = 200  # 24% a year
    assert packs.warnings(r, "ET")[0]["code"] == "country_rate_high"
    assert validation.check(r, "ET") == []  # a warning, not a block


def test_uganda_needs_ten_founders_and_stops_at_thirty():
    c, ms = active_chama(since=TODAY, members=5)
    Chama.objects.filter(pk=c.pk).update(country="UG", currency="UGX")
    c.refresh_from_db()
    assert chamas.min_members(c) == 10
    for i in range(25):
        u = make_user(f"+2567120{i:05d}", f"Member {i}")
        Membership.objects.create(chama=c, user=u, role="member",
                                  member_code=f"JC-TEST-{i:03d}")
    with pytest.raises(Problem) as exc:
        chamas.require_room(c)
    assert exc.value.problem_code == "chama_full"


def test_members_see_what_their_countrys_law_asks(api):
    c, ms = active_chama(since=TODAY, members=5)
    Chama.objects.filter(pk=c.pk).update(country="TZ", currency="TZS")
    api.force_authenticate(ms[-1].user)
    body = api.get(f"/api/v1/chamas/{c.id}/country-rules/?lang=sw").json()
    assert body["country"] == "TZ" and body["min_members"] == 10
    keys = [i["key"] for i in body["items"]]
    assert keys[0] == "size" and body["items"][0]["ok"] is False  # 5 of at least 10
    assert {"quarterly_report", "internal_control", "register_required"} <= set(keys)
    assert date.today()
