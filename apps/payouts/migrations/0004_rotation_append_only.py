from django.db import migrations

from apps.core.guards import append_only, append_only_except
from apps.payouts.models import RotationTurn


class Migration(migrations.Migration):
    dependencies = [("payouts", "0003_rotation")]

    operations = [
        migrations.RunSQL(*append_only("payouts_rotation")),
        migrations.RunSQL(*append_only_except("payouts_rotation_turn", RotationTurn.MUTABLE)),
    ]
