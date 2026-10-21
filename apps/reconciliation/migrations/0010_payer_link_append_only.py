from django.db import migrations

from apps.core.guards import append_only_except


class Migration(migrations.Migration):
    dependencies = [
        ("reconciliation", "0009_alter_match_method_payerlink"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only_except("reconciliation_payer_link",
                                              ["state", "changed_at"])),
    ]
