"""The USSD menu for basic phones (4.9). See docs/flows/ussd.md.

Africa's Talking calls our hook on every step of a session with the whole path typed so
far (``text`` = "1*6*ABCD2345"). The router is stateless: the path is the state. Answers
start with "CON " (the menu continues) or "END " (the session closes).

Read-only: nothing that changes records can be done by USSD. Votes and joining stay on
SMS codes and the app. The caller is known by their number (hashed); numbers we do not
know get a short "not registered" answer and nothing else.
"""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.phones import InvalidPhone, normalise_phone, phone_hash

from . import commands
from .models import UssdRequest

SCREEN = 182  # characters one USSD screen holds


def _fit(text: str) -> str:
    return text if len(text) <= SCREEN else text[:SCREEN - 1] + "…"


def _con(text: str) -> str:
    return "CON " + _fit(text)


def _end(text: str) -> str:
    return "END " + _fit(text)


def _menu(chama_name: str, sw: bool) -> str:
    if sw:
        return (f"{chama_name}\n1. Salio\n2. Kinachodaiwa\n3. Mkopo\n4. Faini\n5. Kanuni\n"
                "6. Hakiki risiti")
    return (f"{chama_name}\n1. Balance\n2. Next due\n3. Loan\n4. Fines\n5. Rules\n"
            "6. Verify a receipt")


def route(user: User, text: str) -> str:
    sw = user.language == "sw"
    parts = [p.strip() for p in (text or "").split("*")] if text else []
    ms = commands._memberships(user)
    if not ms:
        return _end("Hauko kwenye chama chochote kinachoendelea." if sw
                    else "You are not in an active chama.")
    if len(ms) > 1:
        if not parts:
            lines = "\n".join(f"{i}. {m.chama.name}" for i, m in enumerate(ms, start=1))
            return _con(("Chagua chama:\n" if sw else "Choose a chama:\n") + lines)
        if not parts[0].isdigit() or not 1 <= int(parts[0]) <= len(ms):
            return _end("Chagua nambari kwenye orodha." if sw
                        else "Choose a number from the list.")
        m, parts = ms[int(parts[0]) - 1], parts[1:]
    else:
        m = ms[0]
    if not parts:
        return _con(_menu(m.chama.name, sw))
    choice = parts[0]
    answers = {"1": commands._bal, "2": commands._due, "3": commands._loan,
               "4": commands._fines, "5": commands._rules}
    if choice in answers:
        return _end(answers[choice](user, sw, only=m) or
                    ("Hakuna taarifa." if sw else "Nothing to show."))
    if choice == "6":
        if len(parts) == 1:
            return _con("Andika nambari ya risiti:" if sw else "Enter the receipt code:")
        return _end(commands._verify(user, parts[1], sw))
    return _end("Chagua nambari kwenye orodha." if sw else "Choose a number from the menu.")


def handle(phone: str, text: str, session_id: str = "") -> str:
    try:
        e164 = normalise_phone(phone)
    except InvalidPhone:
        return _end("Number not recognised.")
    h = phone_hash(e164)
    since = timezone.now() - timedelta(hours=1)
    if UssdRequest.objects.filter(phone_hash=h, at__gte=since).count() >= \
            settings.USSD_REQUESTS_PER_HOUR:
        return _end("Too many requests. Try again later. / Maombi mengi mno. Jaribu baadaye.")
    user = User.objects.filter(phone_hash=h).first()
    depth = len(text.split("*")) if text else 0
    UssdRequest.objects.create(phone_hash=h, user=user, session_id=session_id[:100],
                               depth=depth)
    if user is None:
        return _end("This number is not registered with JiraniChama. / Nambari hii "
                    "haijasajiliwa na JiraniChama.")
    return route(user, text)
