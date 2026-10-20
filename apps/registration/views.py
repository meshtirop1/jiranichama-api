from datetime import date

from django.http import HttpResponse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.access import require_member
from apps.chamas.models import OFFICIAL_ROLES, Chama
from apps.core.problems import Problem

from . import guides, render
from .models import RegistrationProgress

LANG = OpenApiParameter("lang", str, description="en or sw (default: the person's language)")
COUNTRY = OpenApiParameter("country", str, description="Two-letter country code")


def _lang(request) -> str:
    lang = request.query_params.get("lang") or getattr(request.user, "language", "en")
    return lang if lang in ("en", "sw") else "en"


def _chama(request, pk):
    chama = Chama.objects.filter(pk=pk).first()
    if chama is None:
        raise Problem("not_found", "Chama not found.", status.HTTP_404_NOT_FOUND)
    return chama, require_member(request.user, chama)


class GuideView(APIView):
    """How to register a group in a country, for someone who has not made a chama yet."""

    @extend_schema(parameters=[COUNTRY, LANG], responses=OpenApiTypes.OBJECT)
    def get(self, request):
        country = (request.query_params.get("country")
                   or getattr(request.user, "country", "KE")).upper()
        return Response(guides.guide(country, _lang(request)))


class ProgressSerializer(serializers.Serializer):
    route = serializers.CharField(max_length=40, required=False)
    tick = serializers.CharField(max_length=40, required=False)
    untick = serializers.CharField(max_length=40, required=False)
    certificate_no = serializers.CharField(max_length=60, required=False, allow_blank=True)
    registered_on = serializers.DateField(required=False, allow_null=True)


def _state(chama, membership, lang) -> dict:
    p = RegistrationProgress.objects.filter(chama=chama).first()
    g = guides.guide(chama.country, lang)
    route = p.route if p else "community_group"
    if route not in {r["id"] for r in g["routes"]}:
        route = g["routes"][0]["id"]
    papers = next(r for r in g["routes"] if r["id"] == route)["papers"]
    ticked = list(p.ticked) if p else []
    return {
        "guide": g,
        "route": route,
        "ticked": ticked,
        "certificate_no": p.certificate_no if p else "",
        "registered_on": p.registered_on.isoformat() if p and p.registered_on else None,
        "registered": bool(p and p.registered),
        # Papers this route still needs (neither ticked nor, for app papers, made yet).
        "missing": [k for k in papers if k not in ticked],
        "can_edit": membership.role in OFFICIAL_ROLES,
        # The app reminds the group now and then until it has its certificate.
        "remind": not (p and p.registered) and chama.state == Chama.State.ACTIVE,
    }


class ChamaRegistrationView(APIView):
    """Where the chama is with registering, against its country's guide."""

    @extend_schema(parameters=[LANG], responses=OpenApiTypes.OBJECT)
    def get(self, request, pk):
        chama, me = _chama(request, pk)
        return Response(_state(chama, me, _lang(request)))

    @extend_schema(request=ProgressSerializer, responses=OpenApiTypes.OBJECT)
    def patch(self, request, pk):
        chama, me = _chama(request, pk)
        if me.role not in OFFICIAL_ROLES:
            raise Problem("not_official", "Only an official can update the registration.",
                          status.HTTP_403_FORBIDDEN)
        s = ProgressSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        g = guides.guide(chama.country, "en")
        known = set(guides.PAPERS) | {f"step:{r['id']}:{i}" for r in g["routes"]
                                      for i in range(len(r["steps"]))}
        p, _ = RegistrationProgress.objects.get_or_create(chama=chama)
        if "route" in d:
            if d["route"] not in {r["id"] for r in g["routes"]}:
                raise Problem("unknown_route", "There is no such way to register here.")
            p.route = d["route"]
        for key, add in (("tick", True), ("untick", False)):
            if key in d:
                item = d[key]
                if item not in known:
                    raise Problem("unknown_item", "There is no such paper or step.")
                items = [i for i in p.ticked if i != item]
                p.ticked = items + [item] if add else items
        if "certificate_no" in d:
            p.certificate_no = d["certificate_no"].strip()
            if p.certificate_no and "certificate" not in p.ticked:
                p.ticked = [*p.ticked, "certificate"]
        if "registered_on" in d:
            if d["registered_on"] and d["registered_on"] > date.today():
                raise Problem("date_in_future", "The date cannot be in the future.")
            p.registered_on = d["registered_on"]
        p.updated_by = request.user
        p.save()
        return Response(_state(chama, me, _lang(request)))


class PaperView(APIView):
    """One registration paper as a PDF, filled from the chama's own records."""

    @extend_schema(parameters=[LANG], responses={(200, "application/pdf"): OpenApiTypes.BINARY})
    def get(self, request, pk, paper):
        chama, _ = _chama(request, pk)
        if paper not in render.STORIES:
            raise Problem("not_found", "There is no such paper.", status.HTTP_404_NOT_FOUND)
        lang = _lang(request)
        state = _state(chama, _, lang)
        route = next(r for r in state["guide"]["routes"] if r["id"] == state["route"])
        pdf = render.render(chama, paper, lang, office=route["office"])
        resp = HttpResponse(pdf, content_type="application/pdf")
        resp["Content-Disposition"] = (f'attachment; filename="JiraniChama-'
                                       f'{paper.replace("_", "-")}.pdf"')
        return resp
