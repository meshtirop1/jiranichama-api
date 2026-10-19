from django.db import migrations

from apps.core.guards import append_only


class Migration(migrations.Migration):
    dependencies = [
        ("notify", "0003_ussdrequest"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only("notify_ussd_request")),
    ]
