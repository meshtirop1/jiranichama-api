from django.db import migrations

from apps.core.guards import append_only


class Migration(migrations.Migration):
    dependencies = [("votes", "0003_votesmsnumber")]

    operations = [
        migrations.RunSQL(*append_only("votes_sms_number")),
    ]
