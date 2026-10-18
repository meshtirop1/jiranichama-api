from django.apps import AppConfig


class LedgerConfig(AppConfig):
    name = "apps.ledger"
    label = "ledger"

    def ready(self):
        from . import handlers  # noqa: F401 - registers outbox handlers
