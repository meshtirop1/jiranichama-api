from django.db import migrations

from apps.core.guards import append_only


class Migration(migrations.Migration):
    dependencies = [
        ("feeds", "0007_bankfeed_bankfeedpull"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only("feeds_bank_feed_pull")),
    ]
