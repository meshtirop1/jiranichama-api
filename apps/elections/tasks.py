from celery import shared_task

from . import services


@shared_task
def install_elected() -> int:
    """Hourly: winners whose phone key is now active take their role."""
    return services.install_ready()
