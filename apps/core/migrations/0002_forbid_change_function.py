from django.db import migrations

from apps.core.guards import DROP_FUNCTION_SQL, FORBID_FUNCTION_SQL


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [migrations.RunSQL(FORBID_FUNCTION_SQL, DROP_FUNCTION_SQL)]
