from celery import shared_task


@shared_task
def pull_bank_feeds() -> int:
    """Hourly at five past: read each active bank feed."""
    from .bank_feed import pull_all

    return pull_all()
