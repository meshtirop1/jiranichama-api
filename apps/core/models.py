from django.conf import settings
from django.db import models


class OutboxEvent(models.Model):
    """Transactional outbox: written in the same transaction as the state change it
    announces, published afterwards by a worker. A crash never loses or doubles an event:
    a row is either committed with its change or not at all, and handlers are idempotent.
    """

    topic = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "core_outbox_event"
        indexes = [
            models.Index(
                fields=["created_at"],
                name="outbox_unpublished",
                condition=models.Q(published_at__isnull=True),
            )
        ]

    def __str__(self):
        return f"{self.topic} #{self.pk}"


class IdempotencyRecord(models.Model):
    """The first response to a write sent with an ``Idempotency-Key`` header. A retry
    with the same key gets the same response instead of doing the work twice."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="+")
    key = models.CharField(max_length=100)
    scope = models.CharField(max_length=200)
    request_hash = models.CharField(max_length=64)
    status_code = models.PositiveSmallIntegerField()
    body = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "core_idempotency_record"
        constraints = [
            models.UniqueConstraint(fields=["user", "key"], name="idempotency_user_key"),
        ]
