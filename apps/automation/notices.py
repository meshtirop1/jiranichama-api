"""Telling one person something, once (see models.Notice).

``sms``:
- ``"never"``: the app only;
- ``"away"``: by SMS too when the person has not opened the app in
  ``RECEIPT_SMS_IF_AWAY_DAYS`` days, or never (basic phones);
- ``"always"``: by SMS as well, for things someone must act on soon.

The app's notices replace SMS for app users, which keeps the SMS bill small. Push
notifications will ride on the same notices when Firebase is set up.
"""

from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import Notice


def away(user) -> bool:
    last = user.last_app_at
    return last is None or timezone.now() - last > timedelta(
        days=settings.RECEIPT_SMS_IF_AWAY_DAYS)


def lang(user) -> str:
    return "sw" if user.language == "sw" else "en"


def notify(user, *, key: str, kind: str, text: dict, chama=None, data: dict | None = None,
           sms: str = "away", urgent: bool = False) -> Notice | None:
    """Write the notice and send the SMS, unless this key already went to this person.
    Returns the new notice, or None when it was sent before."""
    from apps.notify.sms import SmsFailed, send_sms

    body = (text.get(lang(user)) or text["en"])[:480]
    try:
        with transaction.atomic():
            n = Notice.objects.create(user=user, chama=chama, kind=kind, key=key[:120],
                                      text=body, data=data or {}, urgent=urgent)
    except IntegrityError:
        return None
    if sms == "always" or (sms == "away" and away(user)):
        try:
            send_sms(user.phone, body if body.startswith("JiraniChama") or chama is None
                     else f"{chama.name}: {body}")
            n.sms_sent = True
            n.save(update_fields=["sms_sent"])
        except SmsFailed:
            pass
    return n


def notify_all(members, **kwargs) -> int:
    """The same notice to several members. ``key`` gets each member's id appended only by
    the caller when it differs per member; the unique key is per person anyway."""
    sent = 0
    for m in members:
        if notify(m.user, **kwargs) is not None:
            sent += 1
    return sent
