from apps.core.outbox import handles


@handles("ledger.entry_appended")
def queue_signature(payload: dict) -> None:
    from .tasks import sign_entry

    sign_entry.delay(payload["entry_id"])
