from django.db import migrations

from apps.core.guards import append_only, append_only_except
from apps.payouts.models import Proposal, UnapprovedDebit


class Migration(migrations.Migration):
    dependencies = [
        ("payouts", "0001_initial"),
        ("core", "0003_forbid_change_except_and_idempotency"),
    ]

    operations = [
        migrations.RunSQL(*append_only_except("payouts_proposal", Proposal.MUTABLE)),
        migrations.RunSQL(*append_only("payouts_approval")),
        migrations.RunSQL(*append_only_except("payouts_unapproved_debit",
                                              UnapprovedDebit.MUTABLE)),
        migrations.RunSQL(*append_only("payouts_money_out_consent")),
    ]
