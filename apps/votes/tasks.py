from celery import shared_task

from . import services


@shared_task
def close_due_votes() -> int:
    """Votes whose closing time has passed: count them and write the result."""
    return services.close_due()
