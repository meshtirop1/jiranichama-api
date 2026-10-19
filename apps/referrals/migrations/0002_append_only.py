from django.db import migrations

from apps.core.guards import append_only_except
from apps.referrals.models import Referral


class Migration(migrations.Migration):
    dependencies = [
        ("referrals", "0001_initial"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only_except("referrals_referral", Referral.MUTABLE)),
    ]
