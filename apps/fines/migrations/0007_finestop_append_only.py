from django.db import migrations

from apps.core.guards import append_only


class Migration(migrations.Migration):
    dependencies = [("fines", "0006_fine_stop")]

    operations = [migrations.RunSQL(*append_only("fines_finestop"))]
