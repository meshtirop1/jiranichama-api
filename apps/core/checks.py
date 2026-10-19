from django.conf import settings
from django.core.checks import Warning, register


@register()
def demo_signin(app_configs, **kwargs):
    """A fixed sign-in code is for test servers only; say so loudly wherever it is on."""
    if settings.DEMO_SIGNIN_CODE:
        return [Warning(
            f"DEMO_SIGNIN_CODE is set: {len(settings.DEMO_PHONES)} demo phone(s) sign in "
            "and approve with a fixed code instead of an SMS.",
            hint="Only on a test server. Unset it in production.", id="jirani.W001")]
    return []
