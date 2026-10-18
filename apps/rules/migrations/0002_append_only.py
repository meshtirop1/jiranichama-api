from django.db import migrations

from apps.core.guards import append_only_except


class Migration(migrations.Migration):
    dependencies = [
        ("rules", "0001_initial"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [migrations.RunSQL(*append_only_except("rules_rule_version", ["state"]))]
