from celery import shared_task

from .services import expire_stale_setups


@shared_task
def expire_setups() -> int:
    return expire_stale_setups()
