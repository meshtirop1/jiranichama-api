"""Meetings, attendance and minutes (5.11). See docs/flows/meetings.md.

The secretary runs meetings (any official when the chama has no secretary). Members check
in with the six-digit code on the secretary's phone, shown as a QR code that changes every
30 seconds, so a screenshot sent to someone at home stops working. Members on basic phones
are marked by the secretary. Closing attendance writes who was present, late, excused and
absent to the ledger and raises the meeting fines the rules set. Minutes go to every
member, who have a week to object; minutes with no objection are sealed on the ledger.
"""

import hashlib
import hmac
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status

from apps.chamas.models import OFFICIAL_ROLES, Chama, Membership
from apps.core.problems import Problem
from apps.fines.models import Fine
from apps.ledger import services as ledger
from apps.notify.sms import SmsFailed, send_sms
from apps.rules.models import version_at

from .models import Apology, Attendance, Meeting, Minutes, MinutesObjection

DEFAULTS = {"absence_fine_cents": 0, "late_fine_cents": 0, "late_after_minutes": 15,
            "apology_excuses": True, "objection_days": 7}
REF_ABSENCE, REF_LATE, REF_MINUTES = "11.1", "11.2", "11.3"
QR_WINDOW_SECONDS = 30
QR_WINDOWS_ACCEPTED = 3  # the current code and the two before it: 60-90 seconds to scan


def settings_from(rules: dict) -> dict:
    return {**DEFAULTS, **(rules.get("meetings") or {})}


def _rules_on(meeting: Meeting) -> tuple:
    version = version_at(meeting.chama, timezone.localtime(meeting.starts_at).date())
    return version, settings_from(version.rules if version else {})


def runs_meetings(m: Membership) -> bool:
    if m.role == Membership.Role.SECRETARY:
        return True
    has_secretary = Membership.objects.filter(
        chama_id=m.chama_id, role=Membership.Role.SECRETARY,
        state=Membership.State.ACTIVE).exists()
    return not has_secretary and m.role in OFFICIAL_ROLES


def _require_runner(m: Membership) -> None:
    if not runs_meetings(m):
        raise Problem("not_allowed", "The secretary runs meetings.", status.HTTP_403_FORBIDDEN)


def _require_state(meeting: Meeting, *states) -> None:
    if meeting.state not in states:
        raise Problem("meeting_state", f"Not possible while the meeting is {meeting.state}.",
                      status.HTTP_409_CONFLICT)


def expected(meeting: Meeting):
    """Members expected at the meeting: current members who had joined by its day."""
    day = timezone.localtime(meeting.starts_at).date()
    return (Membership.objects.filter(chama=meeting.chama, joined_on__lte=day,
                                      state__in=[Membership.State.ACTIVE,
                                                 Membership.State.PROBATION])
            .select_related("user").order_by("member_code"))


def _sms_all(meeting: Meeting, text: dict) -> int:
    sent = 0
    for m in expected(meeting):
        try:
            send_sms(m.user.phone, text.get(m.user.language, text["en"]))
            sent += 1
        except SmsFailed:
            continue
    return sent


def _when(meeting: Meeting) -> str:
    return timezone.localtime(meeting.starts_at).strftime("%d %b %H:%M")


def _where(meeting: Meeting) -> str:
    return meeting.place or meeting.online_link or "-"


# --- scheduling

def schedule(chama: Chama, by: Membership, *, title: str, starts_at, place: str = "",
             online_link: str = "", agenda: str = "") -> Meeting:
    if chama.state != Chama.State.ACTIVE:
        raise Problem("chama_not_active", "Meetings start once the chama is active.",
                      status.HTTP_409_CONFLICT)
    _require_runner(by)
    if starts_at <= timezone.now():
        raise Problem("bad_dates", "A meeting is scheduled for a time still to come.")
    if not (place.strip() or online_link.strip()):
        raise Problem("place_required", "Say where the meeting is, or give the online link.")
    return Meeting.objects.create(chama=chama, scheduled_by=by, title=title.strip()[:120],
                                  starts_at=starts_at, place=place.strip()[:200],
                                  online_link=online_link.strip(), agenda=agenda.strip())


def cancel(meeting: Meeting, by: Membership, *, reason: str) -> Meeting:
    _require_runner(by)
    _require_state(meeting, Meeting.State.SCHEDULED)
    reason = (reason or "").strip()[:300]
    if not reason:
        raise Problem("reason_required", "Say why the meeting is cancelled.")
    meeting.state, meeting.cancel_reason = Meeting.State.CANCELLED, reason
    meeting.save(update_fields=["state", "cancel_reason"])
    transaction.on_commit(lambda: _sms_all(meeting, {
        "en": f"{meeting.chama.name}: the meeting on {_when(meeting)} is cancelled. {reason}",
        "sw": f"{meeting.chama.name}: mkutano wa {_when(meeting)} umeahirishwa. {reason}",
    }))
    return meeting


def remind_due(now=None) -> int:
    """Every 10 minutes: an SMS 2 days and 2 hours before each meeting."""
    now = now or timezone.now()
    sent = 0
    for field, ahead, en, sw in (
        ("reminded_2d_at", timedelta(days=2), "in 2 days", "baada ya siku 2"),
        ("reminded_2h_at", timedelta(hours=2), "in 2 hours", "baada ya saa 2"),
    ):
        due = Meeting.objects.filter(state=Meeting.State.SCHEDULED, starts_at__gt=now,
                                     starts_at__lte=now + ahead, **{f"{field}__isnull": True})
        for meeting in due.select_related("chama"):
            n = Meeting.objects.filter(pk=meeting.pk, **{f"{field}__isnull": True}) \
                .update(**{field: now})
            if not n:
                continue  # another run took it
            if field == "reminded_2d_at" and meeting.starts_at - now <= timedelta(hours=2):
                continue  # the 2-hour reminder will do
            sent += _sms_all(meeting, {
                "en": f"{meeting.chama.name}: {meeting.title} {en}, {_when(meeting)} at "
                      f"{_where(meeting)}. Can't come? Send an apology in the app.",
                "sw": f"{meeting.chama.name}: {meeting.title} {sw}, {_when(meeting)} "
                      f"mahali: {_where(meeting)}. Hutafika? Tuma udhuru kwenye programu.",
            })
    return sent


# --- apologies and attendance

def apologise(meeting: Meeting, by: Membership, *, reason: str,
              member: Membership | None = None) -> Apology:
    """A member's apology before the meeting starts; the secretary may record one for a
    member on a basic phone."""
    member = member or by
    if member.id != by.id:
        _require_runner(by)
    _require_state(meeting, Meeting.State.SCHEDULED)
    if timezone.now() >= meeting.starts_at:
        raise Problem("too_late", "Apologies are sent before the meeting starts.",
                      status.HTTP_409_CONFLICT)
    reason = (reason or "").strip()[:300]
    if not reason:
        raise Problem("reason_required", "Say why you cannot come.")
    try:
        with transaction.atomic():
            return Apology.objects.create(meeting=meeting, membership=member, reason=reason,
                                          recorded_by=by)
    except IntegrityError as exc:
        raise Problem("already_sent", "An apology is already recorded.",
                      status.HTTP_409_CONFLICT) from exc


def start(meeting: Meeting, by: Membership) -> Meeting:
    _require_runner(by)
    _require_state(meeting, Meeting.State.SCHEDULED)
    if timezone.now() < meeting.starts_at - timedelta(hours=1):
        raise Problem("too_early", "A meeting can be opened from an hour before it starts.",
                      status.HTTP_409_CONFLICT)
    meeting.state, meeting.started_at = Meeting.State.IN_PROGRESS, timezone.now()
    meeting.save(update_fields=["state", "started_at"])
    return meeting


def _code(meeting: Meeting, window: int) -> str:
    mac = hmac.new(meeting.qr_secret.encode(), str(window).encode(), hashlib.sha256)
    return f"{int(mac.hexdigest()[:8], 16) % 10**6:06d}"


def _window(now=None) -> int:
    return int((now or timezone.now()).timestamp()) // QR_WINDOW_SECONDS


def check_in_code(meeting: Meeting, by: Membership) -> dict:
    """For the secretary's phone: the code to show, and when it changes."""
    _require_runner(by)
    _require_state(meeting, Meeting.State.IN_PROGRESS)
    now = timezone.now()
    w = _window(now)
    code = _code(meeting, w)
    return {"code": code, "qr": f"jcmeet:{meeting.id}:{code}",
            "changes_in_seconds": QR_WINDOW_SECONDS - int(now.timestamp()) % QR_WINDOW_SECONDS}


def _is_late(meeting: Meeting, at) -> bool:
    _, s = _rules_on(meeting)
    return at > meeting.starts_at + timedelta(minutes=s["late_after_minutes"])


def _attend(meeting, member, *, method, late, by) -> Attendance:
    if not expected(meeting).filter(pk=member.pk).exists():
        raise Problem("not_eligible", "Not a member expected at this meeting.",
                      status.HTTP_403_FORBIDDEN)
    try:
        with transaction.atomic():
            return Attendance.objects.create(meeting=meeting, membership=member,
                                             method=method, late=late, marked_by=by)
    except IntegrityError as exc:
        raise Problem("already_checked_in", "Already marked present.",
                      status.HTTP_409_CONFLICT) from exc


def check_in(meeting: Meeting, member: Membership, *, code: str) -> Attendance:
    _require_state(meeting, Meeting.State.IN_PROGRESS)
    code = (code or "").strip()
    w = _window()
    if not any(hmac.compare_digest(code, _code(meeting, w - i))
               for i in range(QR_WINDOWS_ACCEPTED)):
        raise Problem("code_wrong", "That code is not the one on the secretary's phone now.")
    now = timezone.now()
    return _attend(meeting, member, method=Attendance.Method.QR,
                   late=_is_late(meeting, now), by=member)


def mark(meeting: Meeting, by: Membership, member: Membership, *,
         late: bool | None = None) -> Attendance:
    _require_runner(by)
    _require_state(meeting, Meeting.State.IN_PROGRESS)
    if late is None:
        late = _is_late(meeting, timezone.now())
    return _attend(meeting, member, method=Attendance.Method.SECRETARY, late=late, by=by)


def _raise_fine(meeting, version, member, *, ref, amount, cause) -> Fine | None:
    if amount <= 0:
        return None
    period = timezone.localtime(meeting.starts_at).strftime("%Y-%m")
    entry = ledger.append(
        chama=meeting.chama, type="fine_raised", source="meeting", source_id=meeting.id,
        member=member, amount_cents=amount, period=period,
        rule_version_id=version.id if version else None, rule_refs=[ref],
        decision={"cause": cause, "meeting": str(meeting.id), "title": meeting.title,
                  "starts_at": meeting.starts_at.isoformat()})
    return Fine.objects.create(chama=meeting.chama, membership=member, rule_ref=ref,
                               period=period, amount_cents=amount, raised_entry=entry,
                               meeting=meeting)


def close_attendance(meeting: Meeting, by: Membership) -> Meeting:
    """Who was there goes on the ledger, and the rules' meeting fines are raised."""
    _require_runner(by)
    with transaction.atomic():
        meeting = Meeting.objects.select_for_update().get(pk=meeting.pk)
        _require_state(meeting, Meeting.State.IN_PROGRESS)
        version, s = _rules_on(meeting)
        present = {a.membership_id: a for a in meeting.attendance.all()}
        apologies = set(meeting.apologies.values_list("membership_id", flat=True))
        groups = {"present": [], "late": [], "excused": [], "absent": []}
        for m in expected(meeting):
            a = present.get(m.id)
            if a is not None and a.late:
                groups["late"].append(m.member_code)
                _raise_fine(meeting, version, m, ref=REF_LATE, amount=s["late_fine_cents"],
                            cause="meeting_late")
            elif a is not None:
                groups["present"].append(m.member_code)
            elif m.id in apologies and s["apology_excuses"]:
                groups["excused"].append(m.member_code)
            else:
                groups["absent"].append(m.member_code)
                _raise_fine(meeting, version, m, ref=REF_ABSENCE,
                            amount=s["absence_fine_cents"], cause="meeting_absence")
        entry = ledger.append(
            chama=meeting.chama, type="meeting_closed", source="meeting",
            source_id=meeting.id, rule_version_id=version.id if version else None,
            rule_refs=[REF_ABSENCE, REF_LATE],
            decision={"title": meeting.title, "starts_at": meeting.starts_at.isoformat(),
                      **groups})
        meeting.state, meeting.closed_at = Meeting.State.ATTENDANCE_CLOSED, timezone.now()
        meeting.closed_entry = entry
        meeting.save(update_fields=["state", "closed_at", "closed_entry"])
    return meeting


# --- minutes

def _minutes_hash(text: str, photo_sha256: str) -> str:
    return hashlib.sha256(f"{text}\n{photo_sha256}".encode()).hexdigest()


def latest_minutes(meeting: Meeting) -> Minutes | None:
    return meeting.minutes.order_by("-version").first()


def post_minutes(meeting: Meeting, by: Membership, *, text: str = "", photo=None) -> Minutes:
    """The first minutes, or a revision after objections (which restarts the week)."""
    _require_runner(by)
    text = (text or "").strip()
    if not text and photo is None:
        raise Problem("minutes_empty", "Write the minutes or add a photo of them.")
    photo_sha = ""
    if photo is not None:
        if photo.size > 10 * 1024 * 1024:
            raise Problem("file_too_large", "The photo is larger than 10 MB.")
        digest = hashlib.sha256()
        for chunk in photo.chunks():
            digest.update(chunk)
        photo_sha = digest.hexdigest()
        photo.seek(0)
    with transaction.atomic():
        meeting = Meeting.objects.select_for_update().get(pk=meeting.pk)
        _require_state(meeting, Meeting.State.ATTENDANCE_CLOSED, Meeting.State.MINUTES_PENDING)
        last = latest_minutes(meeting)
        if last is not None and not last.objections.exists():
            raise Problem("minutes_out", "The minutes are out; a revision answers objections.",
                          status.HTTP_409_CONFLICT)
        _, s = _rules_on(meeting)
        minutes = Minutes(meeting=meeting, version=(last.version + 1) if last else 1,
                          text=text, photo_sha256=photo_sha,
                          hash=_minutes_hash(text, photo_sha), written_by=by,
                          objection_until=timezone.now() + timedelta(days=s["objection_days"]))
        if photo is not None:
            minutes.photo.save(f"{meeting.id}-{minutes.version}.jpg", photo, save=False)
        minutes.save()
        meeting.state = Meeting.State.MINUTES_PENDING
        meeting.save(update_fields=["state"])
    days = s["objection_days"]
    transaction.on_commit(lambda: _sms_all(meeting, {
        "en": f"{meeting.chama.name}: minutes of {meeting.title} are in the app. Object "
              f"within {days} days if something is wrong.",
        "sw": f"{meeting.chama.name}: kumbukumbu za {meeting.title} ziko kwenye programu. "
              f"Pinga ndani ya siku {days} kama kuna kosa.",
    }))
    return minutes


def object_to(minutes: Minutes, by: Membership, *, reason: str) -> MinutesObjection:
    meeting = minutes.meeting
    if meeting.state != Meeting.State.MINUTES_PENDING or latest_minutes(meeting) != minutes:
        raise Problem("objection_closed", "These minutes are no longer open to objection.",
                      status.HTTP_409_CONFLICT)
    if timezone.now() > minutes.objection_until:
        raise Problem("objection_closed", "The week to object has passed.",
                      status.HTTP_409_CONFLICT)
    reason = (reason or "").strip()[:500]
    if not reason:
        raise Problem("reason_required", "Say what is wrong in the minutes.")
    try:
        with transaction.atomic():
            return MinutesObjection.objects.create(minutes=minutes, membership=by,
                                                   reason=reason)
    except IntegrityError as exc:
        raise Problem("already_sent", "You already objected to this version.",
                      status.HTTP_409_CONFLICT) from exc


def seal_due(now=None) -> int:
    """Hourly: minutes whose week has passed without objection are sealed (6.6)."""
    now = now or timezone.now()
    n = 0
    for meeting in Meeting.objects.filter(state=Meeting.State.MINUTES_PENDING):
        with transaction.atomic():
            meeting = Meeting.objects.select_for_update().get(pk=meeting.pk)
            minutes = latest_minutes(meeting)
            if (meeting.state != Meeting.State.MINUTES_PENDING or minutes is None
                    or minutes.objection_until > now or minutes.objections.exists()):
                continue
            entry = ledger.append(
                chama=meeting.chama, type="minutes_sealed", source="meeting",
                source_id=meeting.id, rule_refs=[REF_MINUTES],
                decision={"title": meeting.title, "minutes_version": minutes.version,
                          "minutes_hash": minutes.hash,
                          "attendance_receipt": meeting.closed_entry.receipt_code
                          if meeting.closed_entry else None})
            meeting.state, meeting.sealed_entry = Meeting.State.SEALED, entry
            meeting.save(update_fields=["state", "sealed_entry"])
            n += 1
    return n
