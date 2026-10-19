# meetings

Meetings, attendance and minutes (5.11). The flow is in [meetings](../flows/meetings.md).

| File | What it does |
| --- | --- |
| `models.py` | `Meeting` (append-only except its state, timestamps, reminders and ledger links), `Apology`, `Attendance`, `Minutes` (one row per version), `MinutesObjection`, all append-only |
| `services.py` | schedule, cancel, `remind_due`, apologise, start, `check_in_code` / `check_in` (30-second HMAC codes from the meeting's secret), mark, `close_attendance` (ledger entry and fines), `post_minutes`, `object_to`, `seal_due` |
| `views.py` | The meeting endpoints; `MeetingSerializer.people` lists every expected member with their status |
| `tasks.py` | `meeting_reminders` (every 10 minutes), `seal_minutes` (hourly) |

Rules section `meetings` (optional, defaults in `services.DEFAULTS`):
`{absence_fine_cents, late_fine_cents, late_after_minutes, apology_excuses, objection_days}`.

Ledger types: `meeting_closed` (present, late, excused and absent member codes) and
`minutes_sealed` (minutes hash and version).

`fines.Fine.meeting` links a meeting fine to its meeting. Uniqueness is per member, rule and
period for fines without a meeting, and per member, rule and meeting for meeting fines.
