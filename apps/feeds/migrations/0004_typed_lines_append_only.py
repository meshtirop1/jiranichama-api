from django.db import migrations

from apps.core.guards import append_only, append_only_except

OLD = ["state", "confirmed_by_id", "confirmed_at"]
# Typed-in lines may extend the period the statement covers (services.submit_typed_lines).
NEW = OLD + ["period_from", "period_to"]

old_forward, old_reverse = append_only_except("feeds_statement", OLD)
new_forward, new_reverse = append_only_except("feeds_statement", NEW)


class Migration(migrations.Migration):
    dependencies = [
        ("feeds", "0003_typed_lines"),
    ]

    operations = [
        migrations.RunSQL(old_reverse + new_forward, new_reverse + old_forward),
        migrations.RunSQL(*append_only("feeds_typed_line")),
    ]
