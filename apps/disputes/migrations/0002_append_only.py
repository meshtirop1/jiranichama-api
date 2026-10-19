from django.db import migrations

from apps.core.guards import append_only, append_only_except
from apps.disputes.models import Dispute


class Migration(migrations.Migration):
    dependencies = [
        ("disputes", "0001_initial"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only_except("disputes_dispute", Dispute.MUTABLE)),
        migrations.RunSQL(*append_only("disputes_message")),
    ]
