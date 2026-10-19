from django.db import migrations

from apps.core.guards import append_only_except
from apps.staff.models import SupportGrant


class Migration(migrations.Migration):
    dependencies = [
        ("staff", "0003_supportgrant"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only_except("staff_support_grant", SupportGrant.MUTABLE)),
    ]
