from django.db import migrations

from apps.core.guards import append_only_except
from apps.elections.models import Election


class Migration(migrations.Migration):
    dependencies = [
        ("elections", "0001_initial"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only_except("elections_election", Election.MUTABLE)),
    ]
