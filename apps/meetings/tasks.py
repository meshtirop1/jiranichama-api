from celery import shared_task

from . import services


@shared_task
def meeting_reminders() -> int:
    """Every 10 minutes: SMS reminders 2 days and 2 hours before each meeting."""
    return services.remind_due()


@shared_task
def seal_minutes() -> int:
    """Hourly: minutes a week old without objection are sealed."""
    return services.seal_due()
