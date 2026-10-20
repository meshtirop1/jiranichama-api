from celery import shared_task


@shared_task
def issue_monthly_bills() -> int:
    """On the 1st: last month's bill for every active chama."""
    from .services import issue_all

    return issue_all()
