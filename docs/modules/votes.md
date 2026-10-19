# votes

Members' votes. See [Members' votes](../flows/votes.md).

- `models.py`: `Vote` (settings copied from the rules when it opens; only state, tally and
  result entry change), `Ballot` (insert only; no member, key or signature in a secret
  vote; no timestamp), `Turnout` (who voted, insert only).
- `services.py`: `open_vote`, `start_sms_ballot`/`cast_sms`, `start_device_ballot`/
  `cast_device`, `close_due`, `quorum_needed`, `for_subject`. Closing writes a
  `vote_result` entry and tells the subject (payouts: `on_vote_closed`).
- `tasks.py`: `close_due_votes`, hourly.
- `views.py`: `/chamas/{id}/votes/`, `/votes/{id}/`, `/votes/{id}/ballots/`,
  `/votes/{id}/signing-payload/`, `/votes/{id}/signed-ballots/`.
