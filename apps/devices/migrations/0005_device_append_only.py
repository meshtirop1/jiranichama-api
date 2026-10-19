from django.db import migrations

from apps.core.guards import append_only_except
from apps.devices.models import Device


class Migration(migrations.Migration):
    dependencies = [
        ("devices", "0004_device_keys"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only_except("devices_device", Device.MUTABLE)),
    ]
