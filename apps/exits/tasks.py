from celery import shared_task

from . import services


@shared_task
def settle_exits() -> int:
    """07:45: exits whose notice has run are settled; refused settlements proposed again."""
    return services.settle_due()
