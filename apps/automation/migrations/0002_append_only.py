from django.db import migrations

from apps.core.guards import append_only, append_only_except


class Migration(migrations.Migration):
    dependencies = [
        ("automation", "0001_initial"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only_except("automation_notice", ["sms_sent", "read_at"])),
        migrations.RunSQL(*append_only("automation_monthly_pack")),
    ]
