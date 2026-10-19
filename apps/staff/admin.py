"""Registers the Phase 0 models on the staff console (not the default admin site)."""

from django.contrib import admin

from apps.chamas.models import Chama, Membership
from apps.feeds.models import DarajaChannel
from apps.flags.models import FeatureFlag
from apps.ledger.models import LedgerEntry

from . import audit
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


@admin.register(DarajaChannel, site=staff_site)
class DarajaChannelAdmin(AuditedModelAdmin):
    """A chama's own paybill or till. Add it once the chama has authorised it, then use
    "Register with Safaricom" so payments reach us within seconds."""

    list_display = ("shortcode", "kind", "chama", "active", "registered_at")
    fields = ("chama", "shortcode", "kind", "active")
    actions = ["register_with_safaricom"]

    @admin.action(description="Register with Safaricom (C2B callback URLs)")
    def register_with_safaricom(self, request, queryset):
        from apps.feeds import daraja

        for channel in queryset:
            try:
                daraja.register_urls(channel)
                self.message_user(request, f"{channel.shortcode}: registered.")
            except Exception as exc:  # shown to staff, not raised
                self.message_user(request, f"{channel.shortcode}: {exc}", level="error")
            audit.record(actor=request.user, action="register:feeds.darajachannel",
                         target=f"feeds.darajachannel:{channel.pk}",
                         reason="Register C2B URLs with Safaricom", case_ref="daraja")
