from celery import shared_task

from . import daily


@shared_task
def reconcile_daily() -> int:
    """06:00 every day: every active chama's bank against its records."""
    return daily.reconcile_all()


@shared_task
def remind_overdue_statements() -> int:
    """07:00 every day: reminders for overdue bank statements."""
    from . import freshness

    return freshness.remind_all()
