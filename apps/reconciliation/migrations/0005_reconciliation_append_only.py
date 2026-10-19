from django.db import migrations

from apps.core.guards import append_only, append_only_except
from apps.reconciliation.models import ReconciliationGap


class Migration(migrations.Migration):
    dependencies = [("reconciliation", "0004_daily")]

    operations = [
        migrations.RunSQL(*append_only("reconciliation_run")),
        migrations.RunSQL(*append_only_except("reconciliation_gap", ReconciliationGap.MUTABLE)),
    ]
