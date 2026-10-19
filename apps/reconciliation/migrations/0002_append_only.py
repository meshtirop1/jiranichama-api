from django.db import migrations

from apps.core.guards import append_only


class Migration(migrations.Migration):
    dependencies = [
        ("reconciliation", "0001_initial"),
        ("core", "0002_forbid_change_function"),
    ]

    operations = [migrations.RunSQL(*append_only("reconciliation_match"))]
