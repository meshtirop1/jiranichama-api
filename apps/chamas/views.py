from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.contributions.projections import position
from apps.core.idempotency import idempotent
from apps.core.problems import Problem
from apps.core.two_step import ChallengeSentSerializer, ConfirmSerializer, code_sent
from apps.rules import book, validation
from apps.rules.models import RuleVersion
from apps.rules.templates import TEMPLATES

from . import services
from .access import require_founder, require_member, require_official
from .models import Chama, Invite, Membership
from .serializers import (
    BankAccountSerializer,
    ChamaSerializer,
    CreateChamaSerializer,
    InviteCreateSerializer,
    InviteDetailSerializer,
    InviteSerializer,
    MembershipSerializer,
    PositionSerializer,
    RuleBookSerializer,
)


def chama_for(request, pk) -> tuple[Chama, Membership]:
    chama = Chama.objects.filter(pk=pk).first()
    if chama is None:
        raise Problem("not_found", "Chama not found.", status.HTTP_404_NOT_FOUND)
    return chama, require_member(request.user, chama)


def _chama_data(chama, membership):
    return ChamaSerializer(chama, context={"memberships": {chama.id: membership}}).data


class ChamaListView(APIView):
    @extend_schema(responses=ChamaSerializer(many=True))
    def get(self, request):
        mine = {m.chama_id: m for m in Membership.objects.filter(user=request.user).exclude(
            state=Membership.State.EXITED)}
        chamas = Chama.objects.filter(id__in=mine).order_by("name")
        return Response(ChamaSerializer(chamas, many=True,
                                        context={"memberships": mine}).data)

    @extend_schema(request=CreateChamaSerializer, responses={201: ChamaSerializer})
    @idempotent()
    def post(self, request):
        s = CreateChamaSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        chama = services.create_chama(founder=request.user, **s.validated_data)
        membership = Membership.objects.get(chama=chama, user=request.user)
        return Response(_chama_data(chama, membership), status=status.HTTP_201_CREATED)


class ChamaDetailView(APIView):
    @extend_schema(responses=ChamaSerializer)
    def get(self, request, pk):
        chama, membership = chama_for(request, pk)
        data = _chama_data(chama, membership)
        if chama.state == Chama.State.AWAITING_ADOPTION:
            data["adoption"] = services.adoption_status(chama)
        return Response(data)


class BankAccountView(APIView):
    @extend_schema(request=BankAccountSerializer, responses=BankAccountSerializer)
    def put(self, request, pk):
        chama, _ = chama_for(request, pk)
        require_founder(request.user, chama)
        s = BankAccountSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        account = services.set_bank_account(chama, **s.validated_data)
        return Response(BankAccountSerializer(account).data)


class RuleTemplatesView(APIView):
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response({k: {"rules": v, "book_en": [c.as_dict() for c in book.render(v, "en")],
                             "book_sw": [c.as_dict() for c in book.render(v, "sw")]}
                         for k, v in TEMPLATES.items()})


class RulesView(APIView):
    """The chama's rule book: the version in force, or the draft during setup."""

    @extend_schema(responses=RuleBookSerializer)
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        version = RuleVersion.objects.filter(chama=chama, state=RuleVersion.State.IN_FORCE) \
            .order_by("-version_no").first()
        rules = version.rules if version else chama.draft_rules
        return Response({
            "status": "in_force" if version else "draft",
            "version_no": version.version_no if version else None,
            "effective_from": version.effective_from if version else None,
            "hash": version.hash if version else None,
            "rules": rules,
            "book": {"en": [c.as_dict() for c in book.render(rules, "en")],
                     "sw": [c.as_dict() for c in book.render(rules, "sw")]},
        })

    @extend_schema(request=OpenApiTypes.OBJECT, responses=RuleBookSerializer)
    def put(self, request, pk):
        chama, _ = chama_for(request, pk)
        require_founder(request.user, chama)
        try:
            rules = services.set_draft_rules(chama, request.data)
        except validation.RulesInvalid as exc:
            raise Problem("rules_invalid", "Some settings contradict each other or are out of "
                                           "range.", errors=[e.as_dict() for e in exc.errors]
                          ) from exc
        return Response({"rules": rules,
                         "book": {"en": [c.as_dict() for c in book.render(rules, "en")],
                                  "sw": [c.as_dict() for c in book.render(rules, "sw")]}})


class InviteCreateView(APIView):
    @extend_schema(request=InviteCreateSerializer, responses={201: InviteSerializer})
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        if chama.in_setup:
            require_founder(request.user, chama)
        else:
            require_official(request.user, chama)
        s = InviteCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        inv = services.invite(chama, invited_by=request.user, role=s.validated_data["role"],
                              phone=s.validated_data.get("phone"),
                              by_sms=s.validated_data["by_sms"],
                              language=s.validated_data["language"])
        return Response(InviteSerializer(inv).data, status=status.HTTP_201_CREATED)


class InviteAcceptView(APIView):
    """Shows the chama and its rule book, then joins with an SMS code (5.2)."""

    @extend_schema(responses=InviteDetailSerializer)
    def get(self, request, code):
        inv = get_object_or_404(Invite.objects.select_related("chama"), code=code.upper())
        if inv.state != Invite.State.PENDING or inv.expires_at <= timezone.now():
            raise Problem("invite_invalid", "This invite is not valid.",
                          status.HTTP_404_NOT_FOUND)
        rules = services._rules_for_joining(inv.chama)
        lang = request.user.language
        return Response({"chama": {"id": str(inv.chama.id), "name": inv.chama.name,
                                   "type": inv.chama.type},
                         "role": inv.role,
                         "book": [c.as_dict() for c in book.render(rules, lang)]})

    @extend_schema(request=ConfirmSerializer,
                   responses={201: MembershipSerializer, 202: ChallengeSentSerializer})
    def post(self, request, code):
        s = ConfirmSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if "code" not in s.validated_data:
            return code_sent(services.start_accept(code, request.user), request.user)
        m = services.confirm_accept(code, request.user,
                                    challenge_id=s.validated_data["challenge_id"],
                                    sms_code=s.validated_data["code"])
        return Response(MembershipSerializer(m).data, status=status.HTTP_201_CREATED)


class SubmitView(APIView):
    @extend_schema(request=None, responses=ChamaSerializer)
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        require_founder(request.user, chama)
        chama = services.submit_for_adoption(chama)
        return Response(_chama_data(chama, me))


class AdoptView(APIView):
    @extend_schema(request=ConfirmSerializer,
                   responses={200: ChamaSerializer, 202: ChallengeSentSerializer})
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        s = ConfirmSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if "code" not in s.validated_data:
            if not me.is_founding:
                raise Problem("not_founding", "Only founding members confirm the first rules.",
                              status.HTTP_403_FORBIDDEN)
            return code_sent(services.start_adoption(chama, request.user), request.user)
        chama = services.confirm_adoption(chama, me,
                                          challenge_id=s.validated_data["challenge_id"],
                                          sms_code=s.validated_data["code"])
        data = _chama_data(chama, me)
        if chama.state == Chama.State.AWAITING_ADOPTION:
            data["adoption"] = services.adoption_status(chama)
        return Response(data)


class MembersView(APIView):
    @extend_schema(responses=MembershipSerializer(many=True))
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        members = chama.memberships.select_related("user").order_by("member_code")
        return Response(MembershipSerializer(members, many=True).data)


class PositionView(APIView):
    @extend_schema(responses=PositionSerializer)
    def get(self, request, pk):
        membership = Membership.objects.select_related("chama").filter(pk=pk).first()
        if membership is None:
            raise Problem("not_found", "Member not found.", status.HTTP_404_NOT_FOUND)
        # Every member sees every member's position: the records are open (feature 6).
        require_member(request.user, membership.chama)
        return Response({"member_code": membership.member_code,
                         **position(membership, timezone.localdate()).as_dict()})
