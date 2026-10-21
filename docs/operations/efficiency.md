# Efficiency: fast answers, small downloads (2026-10-21)

The owner asked for good Google Play vitals and battery life. Most of a phone's battery and data
goes on the radio, so the API's part is to answer quickly, in few bytes, from few database
queries.

## What we did

- **Compressed answers (gzip).** nginx compresses JSON, text, CSS, JS and SVG answers over
  1 KB (`deploy/test-server/nginx.conf`). The 22 KB fines list goes over the air as about
  5 KB. The full ledger, which every phone downloads to check the hash chain, shrinks about
  the same way. `gzip_vary` keeps caches honest.
- **Rule versions read once.** A member's dues are worked out month by month under the rule
  book in force that month. Before, every month asked the database for its rule version. Now
  `projections.rule_timeline(chama)` reads all versions once, newest first, and
  `_version_on` picks the right one in memory. `dues()` and `position()` take the timeline
  and the member's payments, so callers such as the chama statement load them once for
  everyone.
- **Fines and waivers together.** The fines list prefetches each fine's waivers and their
  votes, so "who approved" and "can I decide" are worked out in memory, not one query per
  fine.

## Measured on the test server (12-member demo chama, warm)

| Endpoint | Time | Queries |
|---|---|---|
| chamas, members, rules, anchors, bills, registration, platform keys | 3-10 ms | 2-7 |
| ledger (153 KB before gzip) | 16 ms | 3 |
| member position / member statement | 18-22 ms | 12-14 |
| fines | 30 ms | 4 |
| chama statement | 125 ms | 95 |

To measure again, open `manage.py shell` on the server and call each
endpoint with DRF's `APIClient` inside `CaptureQueriesContext`, with `HTTP_HOST` set to
the site's host (otherwise Django answers 400).

## Still to do

- **Chama statement:** about 7 queries per member (payments, opening balance, cash waiting,
  open fines). It is opened on demand, so 125 ms is fine for now. Before chamas reach 50+
  members, load those four things for all members in one query each.
- Production nginx needs the same gzip block when the real server is set up.
