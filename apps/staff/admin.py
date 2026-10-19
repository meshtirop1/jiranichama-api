"""Registers the Phase 0 models on the staff console (not the default admin site)."""

from django import forms
from django.contrib import admin

from apps.chamas.models import Chama, Membership
from apps.feeds.models import BankFeed, DarajaChannel
from apps.flags.models import FeatureFlag
from apps.ledger.models import LedgerEntry
from apps.referrals.models import Partner, Referral

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


class BankFeedForm(forms.ModelForm):
    """Credentials are typed in and stored encrypted; they are never shown again."""

    api_key = forms.CharField(required=False, widget=forms.PasswordInput)
    merchant_code = forms.CharField(required=False)
    consumer_secret = forms.CharField(required=False, widget=forms.PasswordInput)
    private_key_pem = forms.CharField(required=False, widget=forms.Textarea,
                                      help_text="The account holder's RSA private key (PEM).")

    class Meta:
        model = BankFeed
        fields = ("chama", "bank_account", "provider", "account_number", "country_code",
                  "start_date", "active")

    def save(self, commit=True):
        feed = super().save(commit=False)
        typed = {k: self.cleaned_data.get(k) for k in
                 ("api_key", "merchant_code", "consumer_secret", "private_key_pem")}
        if all(typed.values()):
            feed.set_credentials(typed)
        if commit:
            feed.save()
        return feed


@admin.register(BankFeed, site=staff_site)
class BankFeedAdmin(AuditedModelAdmin):
    """A chama's bank account read through the bank's API. Add it once the chama has
    authorised read-only access; "Pull now" reads it at once (otherwise hourly)."""

    form = BankFeedForm
    list_display = ("chama", "provider", "account_number", "active", "last_balance_cents",
                    "last_pulled_at", "last_error")
    readonly_fields = ("last_balance_cents", "balance_at", "last_pulled_at", "last_error",
                       "opening_cents")
    actions = ["pull_now"]

    @admin.action(description="Pull now")
    def pull_now(self, request, queryset):
        from apps.feeds.bank_feed import pull

        for feed in queryset:
            p = pull(feed)
            note = p.error or f"{p.new_lines} new lines"
            self.message_user(request, f"{feed}: {note}",
                              level="error" if p.error else "info")
            audit.record(actor=request.user, action="pull:feeds.bankfeed",
                         target=f"feeds.bankfeed:{feed.pk}", reason="Pull bank feed now",
                         case_ref="bank-feed")


@admin.register(Partner, site=staff_site)
class PartnerAdmin(AuditedModelAdmin):
    """Licensed partners only (CBK, SASRA or IRA): check the licence before adding one."""

    list_display = ("name", "product", "regulator", "licence_number", "active")
    fields = ("name", "regulator", "licence_number", "product", "description", "active")


@admin.register(Referral, site=staff_site)
class ReferralAdmin(ReadOnlyAdmin):
    """Referrals the chamas voted for. Pass on only what "Show hand-over" lists, then
    "Mark sent"."""

    list_display = ("chama", "partner", "state", "created_at", "sent_at")
    list_filter = ("state",)
    actions = ["show_handover", "mark_sent"]

    @admin.action(description="Show hand-over (consented only)")
    def show_handover(self, request, queryset):
        from apps.core.problems import Problem
        from apps.referrals.services import handover

        for r in queryset:
            try:
                self.message_user(request, f"{r.partner}: {handover(r)}")
            except Problem as exc:
                self.message_user(request, f"{r.partner}: {exc.detail}", level="error")

    @admin.action(description="Mark sent")
    def mark_sent(self, request, queryset):
        from apps.core.problems import Problem
        from apps.referrals.services import mark_sent

        for r in queryset:
            try:
                mark_sent(r)
                audit.record(actor=request.user, action="sent:referrals.referral",
                             target=f"referrals.referral:{r.pk}",
                             reason="Referral passed to the partner", case_ref="referral")
            except Problem as exc:
                self.message_user(request, f"{r.partner}: {exc.detail}", level="error")
