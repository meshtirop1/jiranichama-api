from django.db import migrations

from apps.core.guards import append_only, append_only_except


class Migration(migrations.Migration):
    dependencies = [
        ("shareout", "0001_initial"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only_except("shareout_shareout",
                                              ["state", "vote_id", "entry_id"])),
        migrations.RunSQL(*append_only_except("shareout_line", ["proposal_id", "entry_id"])),
    ]
