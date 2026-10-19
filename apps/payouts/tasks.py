from celery import shared_task

from . import services


@shared_task
def expire_proposals() -> int:
    """Proposals whose approvals did not come in by the rules' deadline."""
    return services.expire_due()
