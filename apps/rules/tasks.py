from celery import shared_task

from . import proposals


@shared_task
def advance_rule_proposals() -> int:
    """Hourly: notice periods that ended open their vote; scheduled versions start."""
    return proposals.advance()
