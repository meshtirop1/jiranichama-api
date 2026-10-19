"""The admin web console for JiraniChama staff (6.11). See docs/flows/admin-console.md.

Server-rendered, staff only, behind the same two-factor login as the staff site. It shows
the platform's health and the work queues to everyone on staff; a chama's own records
only inside an active support grant from that chama (5.16), and only after the staff
member gives a reason and a ticket number, which go to the chained audit log. Every
change (flags, feeds, partners, referrals) also needs a reason and a ticket number.
Chama records are never changed from here.
"""

from datetime import timedelta
from functools import wraps

from django import forms
from django.contrib import messages
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.chamas.models import Chama, Membership
from apps.feeds.models import BankFeed, DarajaChannel, Statement
from apps.flags.models import FeatureFlag
from apps.ledger.models import LedgerEntry
from apps.staff import audit, support
from apps.staff.models import AuditLog, SupportGrant

VIEW_MINUTES = 60  # how long a logged "open the records" lasts in the session


# --- access -------------------------------------------------------------------------------

def staff_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        user = request.user
        verified = getattr(user, "is_verified", lambda: False)()
        if not (user.is_authenticated and user.is_staff and verified):
            return redirect(f"{reverse('staff:login')}?next={request.get_full_path()}")
        return view(request, *args, **kwargs)
    return wrapped


class ReasonForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}),
                             help_text="Why you are doing this.")
    case_ref = forms.CharField(max_length=64, label="Ticket reference")


def _ip(request):
    return request.META.get("REMOTE_ADDR")


def _record(request, form, action, target):
    audit.record(actor=request.user, action=action, target=target,
                 reason=form.cleaned_data["reason"], case_ref=form.cleaned_data["case_ref"],
                 ip=_ip(request))


# --- dashboard ----------------------------------------------------------------------------

@staff_required
def dashboard(request):
    from apps.notify.models import InboundSms, UssdRequest
    from apps.reconciliation.models import ReconciliationGap
    from apps.referrals.models import Referral

    now = timezone.now()
    day, week = now - timedelta(days=1), now - timedelta(days=7)
    labels = dict(Chama.State.choices)
    chamas = {labels.get(k, k): n for k, n in
              Chama.objects.values_list("state").annotate(n=Count("id"))}
    ctx = {
        "chamas": chamas,
        "chamas_total": sum(chamas.values()),
        "active": chamas.get(labels[Chama.State.ACTIVE], 0),
        "members": Membership.objects.filter(state=Membership.State.ACTIVE).count(),
        "users_week": User.objects.filter(last_app_at__gte=week).count(),
        "entries_day": LedgerEntry.objects.filter(at__gte=day).count(),
        "sms_day": InboundSms.objects.filter(at__gte=day).count(),
        "ussd_day": UssdRequest.objects.filter(at__gte=day).count(),
        "queues": [
            ("Statements waiting for staff", reverse("console:statements"),
             Statement.objects.filter(state__in=[Statement.State.NEEDS_PARSER,
                                                 Statement.State.HELD_MAIL_CHECK]).count()),
            ("Integrity alerts", reverse("console:integrity"),
             ReconciliationGap.objects.filter(state=ReconciliationGap.State.BREACH).count()
             + BankFeed.objects.filter(active=True).exclude(last_error="").count()),
            ("Support access open", reverse("console:support"),
             SupportGrant.objects.filter(state=SupportGrant.State.ACTIVE,
                                         ends_at__gt=now).count()),
            ("Referrals to pass on", reverse("console:referrals"),
             Referral.objects.filter(state=Referral.State.CONSENTED).count()),
        ],
        "audit_problems": audit.verify_audit_chain(),
    }
    return render(request, "console/dashboard.html", ctx)


# --- queues -------------------------------------------------------------------------------

@staff_required
def statements(request):
    rows = (Statement.objects.filter(state__in=[Statement.State.NEEDS_PARSER,
                                                Statement.State.HELD_MAIL_CHECK])
            .select_related("chama", "bank_account").order_by("-created_at")[:200])
    return render(request, "console/statements.html", {"rows": rows})


@staff_required
def integrity(request):
    from apps.ledger.models import Anchor
    from apps.reconciliation.models import ReconciliationGap

    gaps = (ReconciliationGap.objects.filter(state=ReconciliationGap.State.BREACH)
            .select_related("chama").order_by("opened_on"))
    feeds = BankFeed.objects.filter(active=True).exclude(last_error="").select_related("chama")
    anchor = Anchor.objects.order_by("-at").first()
    return render(request, "console/integrity.html",
                  {"gaps": gaps, "feeds": feeds, "anchor": anchor,
                   "audit_problems": audit.verify_audit_chain()})


@staff_required
def support_queue(request):
    support.expire_due()
    rows = (SupportGrant.objects.select_related("chama", "requested_by", "confirmed_by")
            .order_by("-created_at")[:100])
    return render(request, "console/support.html", {"rows": rows})


@staff_required
def referrals(request):
    from apps.core.problems import Problem
    from apps.referrals import services as ref_services
    from apps.referrals.models import Referral

    form = ReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        r = get_object_or_404(Referral, pk=request.POST.get("referral"))
        try:
            ref_services.mark_sent(r)
            _record(request, form, "sent:referrals.referral", f"referrals.referral:{r.pk}")
            messages.success(request, f"{r.partner.name}: marked sent.")
        except Problem as exc:
            messages.error(request, str(exc.detail))
        return redirect("console:referrals")
    rows = []
    for r in (Referral.objects.select_related("chama", "partner", "contact__user")
              .order_by("-created_at")[:100]):
        rows.append({"r": r, "handover": ref_services.handover(r)
                     if r.state == Referral.State.CONSENTED else None})
    return render(request, "console/referrals.html", {"rows": rows, "form": form})


# --- chamas -------------------------------------------------------------------------------

@staff_required
def chamas(request):
    q = (request.GET.get("q") or "").strip()
    qs = Chama.objects.annotate(members=Count("memberships", filter=Q(
        memberships__state=Membership.State.ACTIVE))).order_by("-created_at")
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(member_code_prefix__icontains=q))
    state = request.GET.get("state")
    if state:
        qs = qs.filter(state=state)
    return render(request, "console/chamas.html",
                  {"rows": qs[:200], "q": q, "state": state, "states": Chama.State.choices})


def _viewing_key(chama_id) -> str:
    return f"console_view_{chama_id}"


@staff_required
def chama(request, pk):
    from apps.reconciliation.daily import summary as rec_summary

    c = get_object_or_404(Chama, pk=pk)
    grant = support.active_grant(c)
    form = ReasonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if grant is None:
            raise Http404
        _record(request, form, "view:chama", f"chamas.chama:{c.pk}")
        request.session[_viewing_key(c.pk)] = (timezone.now()
                                               + timedelta(minutes=VIEW_MINUTES)).isoformat()
        return redirect("console:chama", pk=c.pk)
    until = request.session.get(_viewing_key(c.pk))
    viewing = bool(grant and until and timezone.datetime.fromisoformat(until) > timezone.now())
    ctx = {"c": c, "grant": grant, "viewing": viewing, "form": form,
           "members": c.memberships.filter(state=Membership.State.ACTIVE).count(),
           "flags": [f.key for f in FeatureFlag.objects.all() if f.enabled_for_all
                     or c.id in f.chama_allowlist],
           "daraja": DarajaChannel.objects.filter(chama=c),
           "feeds": BankFeed.objects.filter(chama=c)}
    if viewing:
        areas = set(grant.areas)
        ctx["areas"] = areas
        ctx["reconciliation"] = rec_summary(c)
        if "statements" in areas:
            ctx["statements"] = c.statements.order_by("-period_to")[:24]
        if "ledger" in areas:
            ctx["entries"] = c.ledger_entries.order_by("-seq")[:100]
        if "members" in areas:
            ctx["member_rows"] = (c.memberships.select_related("user")
                                  .order_by("member_code"))
        if "rules" in areas:
            from apps.rules import book
            from apps.rules.models import version_at

            v = version_at(c, timezone.localdate())
            ctx["clauses"] = book.render(v.rules, "en") if v else []
        if "disputes" in areas:
            ctx["disputes"] = c.disputes.select_related("raised_by").order_by("-created_at")
    return render(request, "console/chama.html", ctx)


# --- settings staff own -------------------------------------------------------------------

class FlagForm(ReasonForm):
    enabled_for_all = forms.BooleanField(required=False)
    add_prefix = forms.CharField(required=False, label="Turn on for chama (member code prefix)")
    remove_prefix = forms.CharField(required=False, label="Turn off for chama (prefix)")


@staff_required
def flags(request):
    rows = FeatureFlag.objects.order_by("key")
    return render(request, "console/flags.html", {"rows": rows})


@staff_required
def flag(request, key):
    f = get_object_or_404(FeatureFlag, key=key)
    form = FlagForm(request.POST or None, initial={"enabled_for_all": f.enabled_for_all})
    if request.method == "POST" and form.is_valid():
        allow = set(f.chama_allowlist)
        for field, add in (("add_prefix", True), ("remove_prefix", False)):
            prefix = form.cleaned_data[field].strip()
            if prefix:
                c = Chama.objects.filter(member_code_prefix__iexact=prefix).first()
                if c is None:
                    form.add_error(field, "No chama has that prefix.")
                    return render(request, "console/flag.html", {"f": f, "form": form})
                (allow.add if add else allow.discard)(c.id)
        f.enabled_for_all = form.cleaned_data["enabled_for_all"]
        f.chama_allowlist = sorted(allow)
        f.save()
        _record(request, form, "change:flags.featureflag", f"flags.featureflag:{f.key}")
        messages.success(request, f"{f.key} saved.")
        return redirect("console:flags")
    names = dict(Chama.objects.filter(id__in=f.chama_allowlist)
                 .values_list("id", "name"))
    return render(request, "console/flag.html",
                  {"f": f, "form": form, "allowed": [names.get(i, str(i))
                                                     for i in f.chama_allowlist]})


class BankFeedForm(ReasonForm):
    prefix = forms.CharField(label="Chama (member code prefix)")
    account_number = forms.CharField(max_length=40)
    start_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    api_key = forms.CharField(widget=forms.PasswordInput)
    merchant_code = forms.CharField()
    consumer_secret = forms.CharField(widget=forms.PasswordInput)
    private_key_pem = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}),
                                      label="RSA private key (PEM)")


@staff_required
def feeds(request):
    from apps.chamas.services import active_bank_account
    from apps.feeds.bank_feed import pull

    form = BankFeedForm(request.POST or None if request.POST.get("form") == "add" else None)
    pull_form = ReasonForm(request.POST if request.POST.get("form") == "pull" else None)
    if request.method == "POST" and request.POST.get("form") == "add" and form.is_valid():
        c = Chama.objects.filter(member_code_prefix__iexact=form.cleaned_data["prefix"]).first()
        account = active_bank_account(c) if c else None
        if account is None:
            form.add_error("prefix", "No active chama with a bank account has that prefix.")
        else:
            feed = BankFeed(chama=c, bank_account=account,
                            account_number=form.cleaned_data["account_number"],
                            start_date=form.cleaned_data["start_date"])
            feed.set_credentials({k: form.cleaned_data[k] for k in
                                  ("api_key", "merchant_code", "consumer_secret",
                                   "private_key_pem")})
            feed.save()
            _record(request, form, "add:feeds.bankfeed", f"feeds.bankfeed:{feed.pk}")
            messages.success(request, f"Bank feed added for {c.name}.")
            return redirect("console:feeds")
    if request.method == "POST" and request.POST.get("form") == "pull" and pull_form.is_valid():
        feed = get_object_or_404(BankFeed, pk=request.POST.get("feed"))
        p = pull(feed)
        _record(request, pull_form, "pull:feeds.bankfeed", f"feeds.bankfeed:{feed.pk}")
        if p.error:
            messages.error(request, f"{feed}: {p.error}")
        else:
            messages.success(request, f"{feed}: {p.new_lines} new lines.")
        return redirect("console:feeds")
    return render(request, "console/feeds.html", {
        "feeds": BankFeed.objects.select_related("chama").order_by("chama__name"),
        "daraja": DarajaChannel.objects.select_related("chama").order_by("chama__name"),
        "form": form, "pull_form": ReasonForm()})


class PartnerForm(ReasonForm):
    from apps.referrals.models import Partner as _P

    name = forms.CharField(max_length=120)
    regulator = forms.ChoiceField(choices=_P.Regulator.choices)
    licence_number = forms.CharField(max_length=60)
    product = forms.ChoiceField(choices=_P.Product.choices)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


@staff_required
def partners(request):
    from apps.referrals.models import Partner

    form = PartnerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        p = Partner.objects.create(name=d["name"], regulator=d["regulator"],
                                   licence_number=d["licence_number"], product=d["product"],
                                   description=d["description"])
        _record(request, form, "add:referrals.partner", f"referrals.partner:{p.pk}")
        messages.success(request, f"{p.name} added.")
        return redirect("console:partners")
    return render(request, "console/partners.html",
                  {"rows": Partner.objects.order_by("product", "name"), "form": form})


@staff_required
def audit_log(request):
    rows = AuditLog.objects.select_related("actor").order_by("-seq")[:300]
    return render(request, "console/audit.html",
                  {"rows": rows, "problems": audit.verify_audit_chain()})
