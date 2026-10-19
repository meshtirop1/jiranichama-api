from django.db import migrations

from apps.core.guards import append_only_except
from apps.exits.models import Exit


class Migration(migrations.Migration):
    dependencies = [
        ("exits", "0001_initial"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only_except("exits_exit", Exit.MUTABLE)),
    ]
