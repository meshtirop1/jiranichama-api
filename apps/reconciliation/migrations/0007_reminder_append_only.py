from django.db import migrations

from apps.core.guards import append_only_except
from apps.reconciliation.models import StatementReminder


class Migration(migrations.Migration):
    dependencies = [("reconciliation", "0006_statementreminder")]

    operations = [
        migrations.RunSQL(*append_only_except("reconciliation_statement_reminder",
                                              StatementReminder.MUTABLE)),
    ]
