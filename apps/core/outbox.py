import logging
from collections.abc import Callable

from django.db import transaction

from .models import OutboxEvent

log = logging.getLogger(__name__)

_handlers: dict[str, list[Callable[[dict], None]]] = {}


def handles(topic: str):
    """Register a handler. Handlers must be idempotent and quick (usually they enqueue a
    Celery task); they may run more than once for the same event."""

    def register(fn):
        _handlers.setdefault(topic, []).append(fn)
        return fn

    return register


def emit(topic: str, payload: dict) -> OutboxEvent:
    """Record an event inside the caller's transaction and publish it after commit."""
    event = OutboxEvent.objects.create(topic=topic, payload=payload)

    def _kick():
        from .tasks import publish_outbox

        publish_outbox.delay()

    transaction.on_commit(_kick)
    return event


def dispatch(event: OutboxEvent) -> None:
    for handler in _handlers.get(event.topic, []):
        handler(event.payload)
