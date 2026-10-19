# Overdue bank statements (5.5 "Statement freshness")

The rules say how often a bank statement must be accepted (`statements.every_days`, rule
5.1: every 14 days by default). Counting starts from the last day the latest accepted
statement covers, or from when the chama went live if none has been accepted yet:

| Level | When | Who is told |
| --- | --- | --- |
| 0 | Within the period | Nobody: the statement is fresh |
| 1 | Overdue | The treasurer |
| 2 | 7 more days | Every official |
| 3 | 14 more days | Every member: "Records not checked against the bank since {date}" |

- **In the app**: the level shows to the people it is for, on the overview, next to
  the daily bank check. Officials see levels 1 and 2; everyone sees level 3.
  (`GET /chamas/{id}/reconciliation/` carries it as `statement`: `checked_to`,
  `every_days`, `due_on`, `days_overdue`, `level`.)
- **By SMS**: the design sends these as push notifications, not SMS. Push does not exist
  yet, so meanwhile the treasurer (level 1) and the officials (level 2) get **one SMS
  each per overdue spell**. Members are never texted for this.
- **Daily**: a task at 07:00 sends any reminder due. A `StatementReminder` row per spell
  (the due day) and level makes sure each goes once.
- **Back to fresh**: a new accepted statement ends the spell. If it falls overdue again,
  the next spell reminds afresh.
