from django.db import migrations

from apps.core.guards import append_only_except
from apps.ledger.models import Anchor


class Migration(migrations.Migration):
    dependencies = [("ledger", "0006_anchor"),
                    ("core", "0003_forbid_change_except_and_idempotency")]

    operations = [
        migrations.RunSQL(*append_only_except("ledger_anchor", Anchor.MUTABLE)),
    ]
