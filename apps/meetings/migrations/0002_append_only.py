from django.db import migrations

from apps.core.guards import append_only, append_only_except
from apps.meetings.models import Meeting


class Migration(migrations.Migration):
    dependencies = [
        ("meetings", "0001_initial"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only_except("meetings_meeting", Meeting.MUTABLE)),
        migrations.RunSQL(*append_only("meetings_apology")),
        migrations.RunSQL(*append_only("meetings_attendance")),
        migrations.RunSQL(*append_only("meetings_minutes")),
        migrations.RunSQL(*append_only("meetings_minutes_objection")),
    ]
