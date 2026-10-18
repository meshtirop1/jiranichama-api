import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

from apps.core.guards import DROP_EXCEPT_FUNCTION_SQL, FORBID_EXCEPT_FUNCTION_SQL


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_forbid_change_function"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunSQL(FORBID_EXCEPT_FUNCTION_SQL, DROP_EXCEPT_FUNCTION_SQL),
        migrations.CreateModel(
            name="IdempotencyRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False,
                                           verbose_name="ID")),
                ("key", models.CharField(max_length=100)),
                ("scope", models.CharField(max_length=200)),
                ("request_hash", models.CharField(max_length=64)),
                ("status_code", models.PositiveSmallIntegerField()),
                ("body", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "core_idempotency_record"},
        ),
        migrations.AddConstraint(
            model_name="idempotencyrecord",
            constraint=models.UniqueConstraint(fields=("user", "key"), name="idempotency_user_key"),
        ),
    ]
