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
- `VoteSmsNumber` and `sms_invites`/`cast_sms_reply`: the vote by SMS for members without a
  phone key ("Reply YES 4821"). See [SMS commands](../flows/sms-commands.md).

## Protections (2026-10-20)

Some of this changed so that a few people cannot use the rules against one member. See
[protections](../flows/protections.md).

## Richer voting (2026-10-21)

- `Vote` has `kind` (`yes_no` or `choice`), `options`, `details`, `allow_change`,
  `raised_by`, and the tally `abstain`, `option_counts`, `winner`.
- `Ballot` has `round`: a changed vote is a new ballot; `latest_ballots` keeps each voter's
  highest round.
- `Motion` and `MotionSecond`: members' motions and their seconds (`raise_motion`,
  `second`, `withdraw_motion`, `expire_motions` hourly with `close_due_votes`).
- `VoteComment`: members' words on an open vote (`comment`).
- `app_invites`: every eligible member gets a notice when a vote opens.
- Views: `/chamas/{id}/motions/`, `/motions/{id}/second/`, `/motions/{id}/withdraw/`,
  `/votes/{id}/comments/`. See [votes](../flows/votes.md#richer-voting-2026-10-21).
