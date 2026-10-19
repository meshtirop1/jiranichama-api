from django.db import migrations

from apps.core.guards import append_only


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0001_initial"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only("documents_issued")),
        migrations.RunSQL(*append_only("documents_access")),
    ]
