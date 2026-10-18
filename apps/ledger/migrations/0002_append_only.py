from django.db import migrations

from apps.core.guards import append_only

TABLES = ["ledger_entry", "ledger_entry_signature"]


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0001_initial"),
        ("core", "0002_forbid_change_function"),
    ]

    operations = [migrations.RunSQL(*append_only(table)) for table in TABLES]
