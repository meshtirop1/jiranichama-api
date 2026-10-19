from celery import shared_task

from . import services


@shared_task
def loans_daily() -> dict:
    """07:30: interest on due dates, penalties, arrears and default, reminders."""
    return services.run_daily()
