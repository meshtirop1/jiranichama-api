# billing

JiraniChama's monthly bill to each chama.

- `services.py`: prices per currency, active members, the free rules, `compute` (also this month so far), `issue`/`issue_all`, `mark_paid`.
- `models.py`: `Bill` (insert only; state, paid_at and paid_reference may move).
- `tasks.py`: issued on the 1st at 03:00.
- `views.py`: `/chamas/{id}/bills/`. Staff mark payments on the console (`staff/admin.py`).

See [the flow](../flows/billing.md).
