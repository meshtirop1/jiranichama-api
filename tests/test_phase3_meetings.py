"""Meetings, attendance and minutes (5.11)."""

import copy
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.utils import timezone

from apps.core.problems import Problem
from apps.fines.models import Fine
from apps.ledger.models import LedgerEntry
from apps.meetings import services as meetings
from apps.meetings.models import Meeting
from apps.rules.templates import template_for

from .phase1 import active_chama, idem

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def _rules(**m):
    r = copy.deepcopy(template_for("merry_go_round"))
    r["meetings"].update(m)
    return r


def _chama(**m):
    return active_chama(since=TODAY - timedelta(days=60), members=4, rules=_rules(**m))


def _meeting(c, secretary, *, in_minutes=30):
    return meetings.schedule(c, secretary, title="March meeting",
                             starts_at=timezone.now() + timedelta(minutes=in_minutes),
                             place="Church hall", agenda="1. Contributions\n2. AOB")


def _shift(monkeypatch, delta):
    later = timezone.now() + delta
    monkeypatch.setattr(meetings.timezone, "now", lambda: later)
    return later


def test_only_the_secretary_schedules_and_a_place_is_needed():
    c, (chair, treasurer, secretary, member) = _chama()
    with pytest.raises(Problem) as e:
        _meeting(c, chair)
    assert e.value.problem_code == "not_allowed"
    with pytest.raises(Problem) as e:
        meetings.schedule(c, secretary, title="x", starts_at=timezone.now() + timedelta(days=1))
    assert e.value.problem_code == "place_required"
    with pytest.raises(Problem) as e:
        meetings.schedule(c, secretary, title="x", place="Hall",
                          starts_at=timezone.now() - timedelta(minutes=1))
    assert e.value.problem_code == "bad_dates"
    assert _meeting(c, secretary).state == "scheduled"


def test_reminders_two_days_and_two_hours_before(sms_outbox):
    c, (chair, treasurer, secretary, member) = _chama()
    mt = meetings.schedule(c, secretary, title="AGM", place="Hall",
                           starts_at=timezone.now() + timedelta(days=1, hours=12))
    assert meetings.remind_due() == 4 and "AGM in 2 days" in sms_outbox[-1][1]
    assert meetings.remind_due() == 0  # once only
    assert meetings.remind_due(mt.starts_at - timedelta(hours=1)) == 4
    assert "in 2 hours" in sms_outbox[-1][1]


def test_check_in_with_the_changing_code_and_the_secretarys_marks(monkeypatch):
    c, (chair, treasurer, secretary, member) = _chama(late_after_minutes=10)
    mt = _meeting(c, secretary)
    with pytest.raises(Problem) as e:
        meetings.check_in(mt, chair, code="000000")
    assert e.value.problem_code == "meeting_state"
    meetings.start(mt, secretary)
    shown = meetings.check_in_code(mt, secretary)
    assert shown["qr"] == f"jcmeet:{mt.id}:{shown['code']}"
    with pytest.raises(Problem) as e:
        meetings.check_in_code(mt, member)
    assert e.value.problem_code == "not_allowed"

    a = meetings.check_in(mt, chair, code=shown["code"])
    assert a.method == "qr" and a.late is False
    with pytest.raises(Problem) as e:
        meetings.check_in(mt, chair, code=shown["code"])
    assert e.value.problem_code == "already_checked_in"

    _shift(monkeypatch, timedelta(minutes=5))  # the old code is gone after 90 seconds
    with pytest.raises(Problem) as e:
        meetings.check_in(mt, treasurer, code=shown["code"])
    assert e.value.problem_code == "code_wrong"
    _shift(monkeypatch, timedelta(minutes=45))  # 15 minutes after the start
    fresh = meetings.check_in_code(mt, secretary)["code"]
    assert meetings.check_in(mt, treasurer, code=fresh).late is True
    assert meetings.mark(mt, secretary, secretary, late=False).method == "secretary"


def test_closing_raises_absence_and_late_fines_but_not_for_apologies(monkeypatch):
    c, (chair, treasurer, secretary, member) = _chama(absence_fine_cents=20_000,
                                                      late_fine_cents=5_000)
    mt = _meeting(c, secretary)
    meetings.apologise(mt, member, reason="Sick child")
    with pytest.raises(Problem) as e:
        meetings.apologise(mt, member, reason="Again")
    assert e.value.problem_code == "already_sent"
    meetings.start(mt, secretary)
    meetings.mark(mt, secretary, secretary, late=False)
    meetings.mark(mt, secretary, chair, late=True)
    _shift(monkeypatch, timedelta(hours=3))
    with pytest.raises(Problem):
        meetings.apologise(mt, treasurer, reason="Too late")
    mt = meetings.close_attendance(mt, secretary)
    assert mt.state == "attendance_closed"
    d = mt.closed_entry.decision
    assert (d["present"], d["late"], d["excused"], d["absent"]) == (
        [secretary.member_code], [chair.member_code], [member.member_code],
        [treasurer.member_code])
    fines = {f.membership_id: (f.rule_ref, f.amount_cents) for f in Fine.objects.filter(meeting=mt)}
    assert fines == {treasurer.id: ("11.1", 20_000),
                     chair.id: ("11.2", 5_000)}
    assert LedgerEntry.objects.filter(type="fine_raised", source_id=mt.id).count() == 2


def test_two_meetings_in_one_month_fine_twice_and_apologies_may_not_excuse(monkeypatch):
    c, (chair, treasurer, secretary, member) = _chama(absence_fine_cents=10_000,
                                                      apology_excuses=False)
    for _ in range(2):
        mt = _meeting(c, secretary)
        meetings.apologise(mt, member, reason="Travelling")
        meetings.start(mt, secretary)
        for m in (chair, treasurer, secretary):
            meetings.mark(mt, secretary, m, late=False)
        meetings.close_attendance(mt, secretary)
    assert Fine.objects.filter(membership=member, rule_ref="11.1").count() == 2


def test_minutes_objection_revision_and_seal(monkeypatch, sms_outbox,
                                            django_capture_on_commit_callbacks):
    c, (chair, treasurer, secretary, member) = _chama()
    mt = _meeting(c, secretary)
    meetings.start(mt, secretary)
    with pytest.raises(Problem) as e:
        meetings.post_minutes(mt, secretary, text="Too early")
    assert e.value.problem_code == "meeting_state"
    meetings.close_attendance(mt, secretary)
    with django_capture_on_commit_callbacks(execute=True):
        v1 = meetings.post_minutes(mt, secretary, text="Agreed to buy chairs.")
    assert "minutes of March meeting" in sms_outbox[-1][1]
    with pytest.raises(Problem) as e:
        meetings.post_minutes(mt, secretary, text="Changed my mind")
    assert e.value.problem_code == "minutes_out"
    meetings.object_to(v1, member, reason="We agreed on tables, not chairs.")
    assert meetings.seal_due(v1.objection_until + timedelta(hours=1)) == 0  # objected

    v2 = meetings.post_minutes(mt, secretary, text="Agreed to buy tables.")
    assert v2.version == 2
    with pytest.raises(Problem) as e:
        meetings.object_to(v1, member, reason="Old version")
    assert e.value.problem_code == "objection_closed"
    assert meetings.seal_due(v2.objection_until - timedelta(hours=1)) == 0
    assert meetings.seal_due(v2.objection_until + timedelta(minutes=1)) == 1
    mt.refresh_from_db()
    assert mt.state == "sealed"
    assert mt.sealed_entry.decision["minutes_hash"] == v2.hash
    assert mt.sealed_entry.decision["minutes_version"] == 2


def test_cancel_tells_members_and_the_meeting_record_cannot_be_rewritten(
        sms_outbox, django_capture_on_commit_callbacks):
    c, (chair, treasurer, secretary, member) = _chama()
    mt = _meeting(c, secretary)
    with django_capture_on_commit_callbacks(execute=True):
        meetings.cancel(mt, secretary, reason="Funeral in the village")
    assert "is cancelled" in sms_outbox[-1][1]
    with connection.cursor() as cur, pytest.raises(Exception, match="append-only"):
        cur.execute("UPDATE meetings_meeting SET title = 'x' WHERE id = %s", [mt.id])


def test_meetings_through_the_api(api, monkeypatch):
    c, (chair, treasurer, secretary, member) = _chama(absence_fine_cents=20_000)
    api.force_authenticate(secretary.user)
    starts = (timezone.now() + timedelta(minutes=30)).isoformat()
    r = api.post(f"/api/v1/chamas/{c.id}/meetings/",
                 {"title": "March", "starts_at": starts, "place": "Hall"}, format="json",
                 **idem())
    assert r.status_code == 201, r.content
    mid = r.json()["id"]
    assert r.json()["i_run"] is True and len(r.json()["people"]) == 4

    api.force_authenticate(member.user)
    r = api.post(f"/api/v1/meetings/{mid}/apologies/", {"reason": "Sick"}, format="json")
    assert r.json()["my_status"] == "apology"

    api.force_authenticate(secretary.user)
    assert api.post(f"/api/v1/meetings/{mid}/start/").json()["state"] == "in_progress"
    code = api.get(f"/api/v1/meetings/{mid}/check-in-code/").json()["code"]
    api.force_authenticate(chair.user)
    r = api.post(f"/api/v1/meetings/{mid}/check-in/", {"code": code}, format="json")
    assert r.json()["my_status"] == "present"

    api.force_authenticate(secretary.user)
    api.post(f"/api/v1/meetings/{mid}/attendance/",
             {"membership_id": str(secretary.id)}, format="json")
    r = api.post(f"/api/v1/meetings/{mid}/close/")
    people = {p["member_code"]: p["status"] for p in r.json()["people"]}
    assert people[treasurer.member_code] == "absent"
    assert people[member.member_code] == "excused"
    assert r.json()["attendance_receipt"]

    photo = SimpleUploadedFile("m.jpg", b"\xff\xd8\xff fake jpeg", content_type="image/jpeg")
    r = api.post(f"/api/v1/meetings/{mid}/minutes/", {"text": "Notes", "photo": photo},
                 format="multipart")
    assert r.status_code == 201, r.content
    minutes = r.json()["minutes"][0]
    assert minutes["has_photo"] and minutes["version"] == 1

    api.force_authenticate(member.user)
    r = api.get(f"/api/v1/minutes/{minutes['id']}/photo/")
    assert r.status_code == 200 and b"".join(r.streaming_content).startswith(b"\xff\xd8")
    r = api.post(f"/api/v1/minutes/{minutes['id']}/objections/", {"reason": "Wrong"},
                 format="json")
    assert r.json()["minutes"][0]["objections"][0]["reason"] == "Wrong"
    assert Meeting.objects.get(pk=mid).state == "minutes_pending"
