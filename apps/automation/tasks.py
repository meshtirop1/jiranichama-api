from celery import shared_task

from . import chasers, monthly, reminders


@shared_task
def chase_hourly() -> dict:
    """Remind whoever something waits on; open a dispute for an unexplained withdrawal."""
    return chasers.chase_hourly()


@shared_task
def morning() -> dict:
    """Payment reminders, and the treasurer's list of payments waiting to be matched."""
    return {"reminders": reminders.payment_reminders(), **chasers.chase_daily()}


@shared_task
def monthly_pack() -> int:
    """On the 1st: every member's statement and summary, the officials' report, the bill."""
    return monthly.pack_all()
