from django.db import migrations

from apps.core.guards import append_only_except
from apps.investments.models import Investment, Valuation


class Migration(migrations.Migration):
    dependencies = [
        ("investments", "0001_initial"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only_except("investments_investment", Investment.MUTABLE)),
        migrations.RunSQL(*append_only_except("investments_valuation", Valuation.MUTABLE)),
    ]
