"""The staff console: Django admin behind two-factor login.

Reachable only over the staff VPN in production (enforced at the proxy). Staff must have
a confirmed TOTP device; ``OTPAdminSite`` refuses anyone whose session is not verified.
Chama data is read-only here; changes to staff-owned records go through
``AuditedModelAdmin``, which demands a reason and a ticket reference.
"""

from django import forms
from django.contrib import admin
from django_otp.admin import OTPAdminSite

from . import audit
from .models import AuditLog


class StaffSite(OTPAdminSite):
    site_header = "JiraniChama staff console"
    site_title = "JiraniChama staff"
    index_title = "Staff console"


staff_site = StaffSite(name="staff")


def _client_ip(request) -> str | None:
    return request.META.get("REMOTE_ADDR")


class ReasonForm(forms.ModelForm):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}),
                             help_text="Why you are making this change.")
    case_ref = forms.CharField(max_length=64, label="Ticket reference")


class AuditedModelAdmin(admin.ModelAdmin):
    """Every save needs a reason and a ticket reference, and writes an audit entry in the
    same transaction. Deleting is never allowed from the console."""

    form = ReasonForm

    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))
        own = [f for name, opts in fieldsets for f in opts.get("fields", ())]
        if "reason" not in own:
            fieldsets.append(("Why", {"fields": ("reason", "case_ref")}))
        return fieldsets

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        audit.record(
            actor=request.user,
            action=f"{'change' if change else 'add'}:{obj._meta.label_lower}",
            target=f"{obj._meta.label_lower}:{obj.pk}",
            reason=form.cleaned_data["reason"],
            case_ref=form.cleaned_data["case_ref"],
            ip=_client_ip(request),
        )

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditLog, site=staff_site)
class AuditLogAdmin(ReadOnlyAdmin):
    list_display = ("seq", "at", "actor", "action", "target", "case_ref")
    list_filter = ("action",)
    search_fields = ("case_ref", "target")
