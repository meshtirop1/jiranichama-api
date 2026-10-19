# Fine waivers (5.9, rule 3.5)

A fine can be waived, but never quietly.

## Asking

The fined member asks with a reason: `POST /fines/{id}/waiver/`. Only an unpaid fine can
be waived, and only one waiver can be open on a fine at a time.

The rules' `waivers` section sets what it needs: `officials` (default 2) and `member_vote`
(default no). The deadline is the same as for money out (`approvals.deadline_hours`,
default 72 hours). If the chama does not have enough other officials to decide, the
request is refused straight away.

With `member_vote`, a members' vote opens at once ("Waive Mary's fine of KES 200:
hospital?"). The fined member does not vote.

## Deciding

Officials approve with an SMS code that names the member and the amount
(`POST /waivers/{id}/approve/`: first without a code to get one, then with it), or decline
with an optional reason (`/decline/`).

- **Nobody decides a waiver of their own fine** (6 in 5.9): the fined member, who is also
  the one asking, can neither approve nor decline. So an official's fine is decided by the
  other officials. The rules check refuses a `waivers.officials` above the number of
  officials minus one (`waivers_too_many_officials`).
- Once enough officials have approved (and the vote has passed, if there is one), a
  `fine_waived` entry is written for what was still owed. It carries the reason, the rule
  that raised the fine, and who approved, so every member sees it on the ledger and on
  statements. The fine becomes `waived`.
- Declines that make the number of approvals impossible refuse it; so does a failed or
  lapsed vote. The fine stays owed.
- If the fine was paid while the waiver was being decided, the waiver is refused
  ("paid meanwhile"). Nothing is refunded.
- A waiver not decided by the deadline expires (hourly task).

## Seeing

`GET /chamas/{id}/fines/` lists every fine in the chama: who, which rule, which period,
the cause, paid and outstanding, the receipt of the `fine_raised` entry, and the latest
waiver with its approvals. Each member also learns whether they may decide it.
