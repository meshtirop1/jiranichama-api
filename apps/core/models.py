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
