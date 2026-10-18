"""Registers the Phase 0 models on the staff console (not the default admin site)."""

from django.contrib import admin

from apps.chamas.models import Chama, Membership
from apps.flags.models import FeatureFlag
from apps.ledger.models import LedgerEntry

from .site import AuditedModelAdmin, ReadOnlyAdmin, staff_site


@admin.register(FeatureFlag, site=staff_site)
class FeatureFlagAdmin(AuditedModelAdmin):
    list_display = ("key", "enabled_for_all", "updated_at")
    fields = ("key", "description", "enabled_for_all", "chama_allowlist")


@admin.register(LedgerEntry, site=staff_site)
class LedgerEntryAdmin(ReadOnlyAdmin):
    list_display = ("chama", "seq", "type", "member_code", "amount_cents", "at", "receipt_code")
    list_filter = ("type",)
    search_fields = ("receipt_code", "hash")


@admin.register(Chama, site=staff_site)
class ChamaAdmin(ReadOnlyAdmin):
    list_display = ("name", "type", "state", "mode", "member_code_prefix", "created_at")


@admin.register(Membership, site=staff_site)
class MembershipAdmin(ReadOnlyAdmin):
    list_display = ("member_code", "chama", "role", "state")
