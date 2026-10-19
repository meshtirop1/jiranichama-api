"""Numbers for running the chama (Phase 5)."""

from datetime import date

import pytest

from apps.fines.services import raise_late_fines
from apps.ledger import services as ledger
from apps.reconciliation import services as recon
from apps.reports.analytics import report

from .phase1 import active_chama, credit

pytestmark = pytest.mark.django_db


def test_months_show_due_paid_on_time_and_late_and_who_is_behind():
    c, (chair, treasurer, secretary) = active_chama(since=date(2026, 1, 1))
    # January: chair on time, treasurer late, secretary not at all.
    recon.match_credit(credit(c, amount_cents=100_000, value_date=date(2026, 1, 3),
                              narration=f"pay {chair.member_code}"))
    recon.match_credit(credit(c, amount_cents=100_000, value_date=date(2026, 1, 20),
                              narration=f"pay {treasurer.member_code}"))
    raise_late_fines(c, date(2026, 1, 10))
    r = report(c, months=2, today=date(2026, 2, 1))
    jan = next(m for m in r["months"] if m["period"] == "2026-01")
    assert jan["due_cents"] == 300_000 and jan["paid_cents"] == 200_000
    assert jan["paid_pct"] == 67
    assert jan["on_time_cents"] == 100_000 and jan["late_cents"] == 100_000
    assert jan["fines_raised_cents"] > 0
    codes = [b["member_code"] for b in r["behind"]]
    assert secretary.member_code in codes and chair.member_code not in codes
    assert r["loans"] is None and r["members"] == 3


def test_meeting_attendance_is_taken_from_the_records():
    c, (chair, *_) = active_chama(since=date(2026, 1, 1))
    ledger.append(chama=c, type="meeting_closed", source="meeting",
                  decision={"title": "AGM", "starts_at": "2026-01-15T10:00:00+03:00",
                            "present": ["A", "B"], "late": ["C"], "excused": [],
                            "absent": ["D"]})
    [m] = report(c, months=3, today=date(2026, 2, 1))["meetings"]
    assert (m["attended"], m["expected"], m["attendance_pct"]) == (3, 4, 75)


def test_any_member_reads_it(api):
    c, (chair, treasurer, secretary) = active_chama(since=date(2026, 1, 1))
    api.force_authenticate(secretary.user)
    r = api.get(f"/api/v1/chamas/{c.id}/analytics/?months=3")
    assert r.status_code == 200 and len(r.json()["months"]) == 3
    assert r.json()["money"]["contributed_cents"] == 0
