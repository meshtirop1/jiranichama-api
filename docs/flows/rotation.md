# Merry-go-round rotation (5.6 step 9, rules 8.1 and 8.2)

A cycle gives every member one turn at the pot: every member's monthly contribution
(contribution × members in the cycle). The rules (`rotation` section) set two things:

- **order**: `fixed` (the officials give the order) or `draw` (the app draws it);
- **arrears**: what happens when the member whose turn it is is behind on contributions:
  `skip`, `deduct` or `block`.

Merry-go-round chamas get `{"order": "draw", "arrears": "skip"}` from their template;
other chama types have no rotation. Both settings show in the rule book (8.1, 8.2) and
in the app's rules editor.

## Starting a cycle

`POST /chamas/{id}/rotation/` (any official; `order` with every member's id once, for a
fixed order). A new cycle starts only when the last one has no pending turn.

**The draw is checkable.** The seed is the chama's chain fingerprint at that moment
(the latest entry's hash). The order sorts member codes by SHA-256 of `"{seed}:{code}"`.
A `rotation_set` ledger entry records the cycle, method, seed and order. Anyone with the
seed and the member codes can re-run the draw and get the same order, and nobody can
choose the seed, because it is the hash of everything recorded so far.

## Whose turn, and the payout

`GET /chamas/{id}/rotation/` shows the cycle, the order with each turn's state
(pending, paid, skipped), the pot and the next position.

`POST /chamas/{id}/rotation/next/` (an official) fills in the payout proposal for the next
turn: the member, the pot, and "Merry-go-round turn 3 of 12, cycle 1". It then goes
through [money out](money-out.md) like any payout: the rules check, approvals (the
member being paid never approves), Paid, and the bank debit matched back. When it is
paid, the turn is paid and the next member is up. If the proposal is declined, expires
or is cancelled, the turn can be proposed again.

## A member behind on contributions (rule 8.2)

- **skip**: their turn is marked skipped, with the amount they owe, and they get a new
  turn at the end of the order. The next member is proposed instead. Each member is
  skipped at most once per request, so a chama where everyone is behind gets an answer
  rather than a loop.
- **deduct**: they are paid the pot less what they owe. Two entries record it: a payout
  of the **whole pot**, and contribution entries for what they owed (judged by the rules
  like any payment). Never a smaller payout with no explanation.
- **block**: no proposal; the rotation waits until they catch up (`turn_blocked`).

## Not yet

Bidding for turns, and disputes about a draw (5.14). The draw can already be re-run by
anyone.
