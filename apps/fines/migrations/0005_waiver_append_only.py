from django.db import migrations

from apps.core.guards import append_only, append_only_except
from apps.fines.models import Waiver


class Migration(migrations.Migration):
    dependencies = [
        ("fines", "0004_waiver_waiverdecision"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only_except("fines_waiver", Waiver.MUTABLE)),
        migrations.RunSQL(*append_only("fines_waiver_decision")),
    ]
