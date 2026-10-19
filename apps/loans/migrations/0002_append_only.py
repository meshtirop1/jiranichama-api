from django.db import migrations

from apps.core.guards import append_only, append_only_except
from apps.loans.models import Guarantee, Loan


class Migration(migrations.Migration):
    dependencies = [
        ("loans", "0001_initial"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only_except("loans_loan", Loan.MUTABLE)),
        migrations.RunSQL(*append_only_except("loans_guarantee", Guarantee.MUTABLE)),
        migrations.RunSQL(*append_only("loans_reminder")),
    ]
