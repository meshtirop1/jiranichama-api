from django.contrib.postgres.fields import ArrayField
from django.db import models


class FeatureFlag(models.Model):
    """A switch for unfinished or pilot-only features. Off unless turned on, either for
    everyone or for the chamas on its allowlist."""

    key = models.SlugField(max_length=64, unique=True)
    description = models.CharField(max_length=255, blank=True, default="")
    enabled_for_all = models.BooleanField(default=False)
    chama_allowlist = ArrayField(models.UUIDField(), default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "flags_feature_flag"

    def __str__(self):
        return self.key


def is_enabled(key: str, chama_id=None) -> bool:
    flag = FeatureFlag.objects.filter(key=key).first()
    if flag is None:
        return False
    if flag.enabled_for_all:
        return True
    return chama_id is not None and chama_id in flag.chama_allowlist
