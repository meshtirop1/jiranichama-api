"""SMS commands for members on basic phones (4.9). See docs/flows/sms-commands.md.

A member texts a word to the chama shortcode and gets the answer by SMS:

    BAL / SALIO        savings, months behind and fines owed, per chama
    DUE / DENI         what is due next, and when
    FINES / FAINI      unpaid fines
    RULES / KANUNI     the contribution, due day and late fine
    VERIFY <code> / HAKIKI <code>   check a receipt (8 characters) or a statement (10)
    YES <n> / NDIYO <n>, NO <n> / HAPANA <n>   vote, with the number from the vote SMS
    HELP / MSAADA      the list

Read-only except votes. The sender is known by their number (hashed); numbers we do not
know get no answer. Each number may send ``SMS_COMMANDS_PER_HOUR`` messages an hour.
"""

import re
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.phones import InvalidPhone, normalise_phone, phone_hash
from apps.chamas.models import Membership

from .models import InboundSms
from .sms import SmsFailed, send_sms

WORDS = {
    "BAL": "bal", "SALIO": "bal",
    "DUE": "due", "DENI": "due",
    "FINES": "fines", "FAINI": "fines",
    "RULES": "rules", "KANUNI": "rules",
    "VERIFY": "verify", "HAKIKI": "verify",
    "YES": "yes", "NDIYO": "yes", "NDIO": "yes",
    "NO": "no", "HAPANA": "no",
    "HELP": "help", "MSAADA": "help",
}


def _kes(cents: int) -> str:
    return f"KES {cents / 100:,.0f}" if cents % 100 == 0 else f"KES {cents / 100:,.2f}"


def _memberships(user):
    return list(Membership.objects.filter(user=user, state__in=[
        Membership.State.ACTIVE, Membership.State.PROBATION]).select_related("chama")
        .order_by("chama__name")[:3])


def _bal(user, sw: bool) -> str:
    from apps.contributions.projections import position

    today = timezone.localdate()
    parts = []
    for m in _memberships(user):
        p = position(m, today)
        parts.append(f"{m.chama.name}: akiba {_kes(p.contributed_cents)}, deni "
                     f"{_kes(p.arrears_cents)}, faini {_kes(p.fines_cents)}" if sw else
                     f"{m.chama.name}: saved {_kes(p.contributed_cents)}, behind "
                     f"{_kes(p.arrears_cents)}, fines {_kes(p.fines_cents)}")
    return ". ".join(parts) + "." if parts else ""


def _due(user, sw: bool) -> str:
    from apps.contributions.projections import position
    from apps.rules import calendar
    from apps.rules.models import version_at

    today = timezone.localdate()
    parts = []
    for m in _memberships(user):
        p = position(m, today)
        version = version_at(m.chama, today)
        if p.next_due_period is None or version is None:
            continue
        due = calendar.due_date(p.next_due_period, version.rules).strftime("%d %b")
        behind = (f" (+{_kes(p.arrears_cents)} " + ("ya nyuma)" if sw else "behind)")
                  if p.arrears_cents else "")
        parts.append(f"{m.chama.name}: {_kes(p.next_due_cents)} kabla ya {due}{behind}" if sw
                     else f"{m.chama.name}: {_kes(p.next_due_cents)} by {due}{behind}")
    if not parts:
        return "Hakuna kinachodaiwa." if sw else "Nothing due."
    code = ", ".join(m.member_code for m in _memberships(user))
    tail = f" Tumia nambari {code}." if sw else f" Use code {code}."
    return ". ".join(parts) + "." + tail


def _fines(user, sw: bool) -> str:
    from apps.fines.models import Fine

    parts = []
    for m in _memberships(user):
        open_ = [f for f in Fine.objects.filter(membership=m, state=Fine.State.RAISED)
                 if f.outstanding_cents > 0]
        total = sum(f.outstanding_cents for f in open_)
        parts.append(f"{m.chama.name}: faini {len(open_)}, {_kes(total)}" if sw else
                     f"{m.chama.name}: {len(open_)} fine(s), {_kes(total)}")
    return ". ".join(parts) + "." if parts else ""


def _rules(user, sw: bool) -> str:
    from apps.rules.models import version_at

    today = timezone.localdate()
    parts = []
    for m in _memberships(user):
        v = version_at(m.chama, today)
        if v is None:
            continue
        c, f = v.rules["contributions"], v.rules["fines"]["late_contribution"]
        fine = (_kes(f["amount_cents"]) if f["enabled"] else ("hakuna" if sw else "none"))
        parts.append(f"{m.chama.name}: {_kes(c['amount_cents'])} kila mwezi kabla ya tarehe "
                     f"{c['due_day']}, siku {c['grace_days']} za ziada, faini {fine}" if sw
                     else f"{m.chama.name}: {_kes(c['amount_cents'])} a month by day "
                     f"{c['due_day']}, {c['grace_days']} grace days, late fine {fine}")
    return ". ".join(parts) + "." if parts else ""


def _verify(user, arg: str, sw: bool) -> str:
    from apps.documents.services import find, normalise_code
    from apps.ledger.models import LedgerEntry

    raw = "".join(ch for ch in (arg or "").upper() if ch.isalnum())
    # Statement codes (10) forgive O/I/L for 0/1/1; receipt codes (8) are Base32 letters
    # and digits 2-7, so they are taken as typed.
    code = normalise_code(arg) if len(raw) == 10 else raw
    if len(code) == 10:
        doc = find(code)
        if doc is not None:
            return (f"HALALI: taarifa ya {doc.chama.name}, {doc.period_from} hadi "
                    f"{doc.period_to}." if sw else
                    f"GENUINE: {doc.chama.name} statement, {doc.period_from} to "
                    f"{doc.period_to}.")
    elif len(code) == 8:
        # Receipts are checked only in the sender's own chamas: they are not public.
        chamas = [m.chama_id for m in _memberships(user)]
        e = (LedgerEntry.objects.filter(chama_id__in=chamas, receipt_code=code)
             .select_related("chama").first())
        if e is not None:
            day = timezone.localdate(e.at).strftime("%d %b")
            what = e.type.replace("_", " ")
            return (f"HALALI: {what} {_kes(abs(e.amount_cents))}, {e.member_code or ''} "
                    f"{e.chama.name}, {day}." if sw else
                    f"GENUINE: {what} {_kes(abs(e.amount_cents))}, {e.member_code or ''} "
                    f"{e.chama.name}, {day}.")
    return ("Hatukupata nambari hiyo. Hakiki na ujaribu tena." if sw else
            "No record has that code. Check it and try again.")


def _vote(user, choice: str, arg: str, sw: bool) -> str:
    from apps.core.problems import Problem
    from apps.votes.services import cast_sms_reply

    try:
        vote = cast_sms_reply(user, choice=choice, number=arg.strip())
    except Problem as p:
        reasons = {
            "already_voted": ("Umeshapiga kura. Kura ya kwanza ndiyo inahesabiwa.",
                              "You have already voted. The first vote counts."),
            "vote_closed": ("Kura hii imefungwa.", "This vote has closed."),
            "use_device_key": ("Piga kura kwenye programu kwa alama ya kidole.",
                               "Vote in the app with your fingerprint."),
        }
        sw_text, en_text = reasons.get(p.problem_code, (
            "Nambari ya kura haijulikani.", "That vote number is not known."))
        return sw_text if sw else en_text
    word = ("NDIYO" if choice == "yes" else "HAPANA") if sw else choice.upper()
    return (f"Kura yako {word} imepokelewa: {vote.question}" if sw else
            f"Your vote {word} is recorded: {vote.question}")


def _help(sw: bool) -> str:
    return ("Tuma: SALIO, DENI, FAINI, KANUNI, HAKIKI <nambari>, au NDIYO/HAPANA <nambari> "
            "kupiga kura." if sw else
            "Send: BAL, DUE, FINES, RULES, VERIFY <code>, or YES/NO <number> to vote.")


def answer(user: User, text: str) -> tuple[str, str]:
    """(command, reply) for one message from a known member."""
    words = (text or "").strip().split(None, 1)
    first = words[0].upper() if words else ""
    arg = words[1] if len(words) > 1 else ""
    command = WORDS.get(first, "help")
    # Answer in the language of the word they used; otherwise their own language.
    sw = first in {"SALIO", "DENI", "FAINI", "KANUNI", "HAKIKI", "NDIYO", "NDIO", "HAPANA",
                   "MSAADA"} or (first not in WORDS and user.language == "sw")
    if command == "bal":
        reply = _bal(user, sw)
    elif command == "due":
        reply = _due(user, sw)
    elif command == "fines":
        reply = _fines(user, sw)
    elif command == "rules":
        reply = _rules(user, sw)
    elif command == "verify":
        reply = _verify(user, arg, sw)
    elif command in ("yes", "no"):
        reply = _vote(user, command, arg, sw)
    else:
        reply = _help(sw)
    if not reply:
        reply = ("Hauko kwenye chama chochote kinachoendelea." if sw else
                 "You are not in an active chama.")
    return command, f"JiraniChama: {reply}"


_INVITE = re.compile(r"[A-Z0-9]{6}")
SW_WORDS = {"SALIO", "DENI", "FAINI", "KANUNI", "HAKIKI", "NDIYO", "NDIO", "HAPANA", "MSAADA"}


def _join(e164: str, h: str, code: str, sw: bool, provider_id: str, user) -> str | None:
    """YES <invite code>: join from a basic phone (5.2a). Anyone may try, but only the
    number the invite was sent to succeeds; others get no answer."""
    from apps.chamas.services import accept_by_sms, welcome_text
    from apps.core.problems import Problem

    try:
        membership = accept_by_sms(code, e164)
    except Problem as p:
        InboundSms.objects.create(phone_hash=h, user=user, command="join_refused",
                                  provider_id=provider_id)
        if user is None or p.problem_code == "invite_invalid":
            return None  # never text a stranger, or confirm a code exists
        reply = ("JiraniChama: tayari wewe ni mwanachama." if sw
                 else "JiraniChama: you are already a member.")
    else:
        reply = welcome_text(membership)
        user = membership.user
    log = InboundSms.objects.create(phone_hash=h, user=user, command="join",
                                    provider_id=provider_id)
    try:
        send_sms(e164, reply)
    except SmsFailed:
        return None
    InboundSms.objects.filter(pk=log.pk).update(replied=True)
    return reply


def handle(from_number: str, text: str, provider_id: str = "") -> str | None:
    """One inbound message. Returns the reply sent, or None (unknown number, or over the
    hourly limit)."""
    try:
        e164 = normalise_phone(from_number)
    except InvalidPhone:
        return None
    h = phone_hash(e164)
    since = timezone.now() - timedelta(hours=1)
    if InboundSms.objects.filter(phone_hash=h, at__gte=since).count() >= \
            settings.SMS_COMMANDS_PER_HOUR:
        InboundSms.objects.create(phone_hash=h, command="limited", provider_id=provider_id)
        return None
    user = User.objects.filter(phone_hash=h).first()
    words = (text or "").strip().split()
    if (len(words) == 2 and WORDS.get(words[0].upper()) == "yes"
            and _INVITE.fullmatch(words[1].upper())):
        return _join(e164, h, words[1], words[0].upper() in SW_WORDS, provider_id, user)
    if user is None:
        InboundSms.objects.create(phone_hash=h, command="unknown", provider_id=provider_id)
        return None
    command, reply = answer(user, text)
    log = InboundSms.objects.create(phone_hash=h, user=user, command=command,
                                    provider_id=provider_id)
    try:
        send_sms(e164, reply)
    except SmsFailed:
        return None
    InboundSms.objects.filter(pk=log.pk).update(replied=True)
    return reply
