import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import OutboxEvent
from .outbox import dispatch

log = logging.getLogger(__name__)

BATCH = 200


@shared_task
def publish_outbox() -> int:
    """Publish unpublished events, oldest first. ``skip_locked`` lets several workers run
    this at once without handing the same event to two of them."""
    published = 0
    with transaction.atomic():
        events = list(
            OutboxEvent.objects.select_for_update(skip_locked=True)
            .filter(published_at__isnull=True)
            .order_by("created_at", "id")[:BATCH]
        )
        for event in events:
            event.attempts += 1
            try:
                dispatch(event)
            except Exception as exc:  # noqa: BLE001 - one bad event must not block the rest
                log.exception("outbox handler failed for %s", event)
                event.last_error = repr(exc)[:2000]
                event.save(update_fields=["attempts", "last_error"])
                continue
            event.published_at = timezone.now()
            event.save(update_fields=["attempts", "published_at"])
            published += 1
    return published
