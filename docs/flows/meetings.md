# Meetings, attendance and minutes (5.11, rules 11.1-11.3)

The secretary runs meetings. If the chama has no secretary, any official can.

## Schedule

`POST /chamas/{id}/meetings/` with a title, start time (in the future), place or online
link, and agenda. Members get an SMS **2 days** and **2 hours** before (task every 10
minutes). A meeting scheduled less than 2 hours ahead gets only the 2-hour reminder.

What members were told (title, time, place, agenda) cannot be edited. If plans change,
the secretary **cancels** it with a reason (`/meetings/{id}/cancel/`). Members get an SMS,
and the secretary schedules a new one.

## Apologies

Before the meeting starts, a member sends an apology with a reason
(`/meetings/{id}/apologies/`). The secretary can record one for a member on a basic phone.
`meetings.apology_excuses` (default yes) decides whether an apology avoids the absence
fine.

## Attendance

1. The secretary **opens** the meeting (`/start/`), from an hour before the start.
2. The secretary's phone shows a **six-digit code and its QR code**
   (`GET /check-in-code/`). The code changes every 30 seconds and is accepted for about
   90 seconds, so a photo sent to someone at home soon stops working.
3. Members **check in** by scanning the QR code or typing the code (`/check-in/`).
4. Members on basic phones, or with a flat battery, are **marked by the secretary**
   (`/attendance/`).
5. Anyone arriving more than `late_after_minutes` (default 15) after the start is
   recorded as late. The secretary can mark someone late or on time explicitly.

## Closing attendance

`/close/` takes every member who had joined by the meeting day and puts each one in one
group: **present**, **late**, **excused** (apology, if the rules excuse it) or **absent**.
- A `meeting_closed` ledger entry records the four groups by member code, so the list
  cannot be changed later.
- Fines from the rules are raised at once: `absence_fine_cents` (rule 11.1) for absent
  members, `late_fine_cents` (rule 11.2) for late ones. Each is a normal `fine_raised`
  entry, paid like any fine. Both default to 0, so a chama turns them on in its rules.
- A fine is raised **once per member, rule and meeting**, so two meetings in one month
  can each fine a member.

## Minutes

1. The secretary writes the minutes, or uploads a photo of handwritten ones, or both
   (`/minutes/`, JSON or multipart). Every member gets an SMS.
2. Members have `objection_days` (default 7) to object with a reason
   (`POST /minutes/{id}/objections/`).
3. If anyone objects, the minutes wait. The secretary posts a **revised version**, which
   restarts the week. Old versions and their objections stay visible. (Disputes, 5.14,
   will add a way to settle a disagreement that the secretary does not resolve.)
4. Minutes whose week has passed without objection are **sealed** (hourly task): a
   `minutes_sealed` ledger entry carries the minutes' hash (SHA-256 of the text and the
   photo's hash), the version, and the attendance receipt.

The photo is served only to members (`GET /minutes/{id}/photo/`).

## States

`scheduled` → `in_progress` → `attendance_closed` → `minutes_pending` → `sealed`, or
`scheduled` → `cancelled`.

## Not yet

- SMS confirmation by members marked present by the secretary.
- A monthly cap across meeting fines (only the late-contribution fine has a cap).
- Push notifications; reminders go by SMS for now.
