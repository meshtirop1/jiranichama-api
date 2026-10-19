"""Money-out endpoints (5.6, 5.7). Every member sees every proposal and every unapproved
debit; officials propose, approve, decline, mark paid, link and explain."""

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.access import require_member
from apps.chamas.models import Chama, Membership
from apps.chamas.views import chama_for
from apps.core.idempotency import idempotent
from apps.core.problems import Problem
from apps.core.two_step import ChallengeSentSerializer, ConfirmSerializer, code_sent

from . import rotation as rot
from . import services
from .models import Approval, MoneyOutConsent, Proposal, RotationTurn, UnapprovedDebit


def _name(m: Membership) -> str:
    return (m.user.display_name or m.member_code) if m.user_id else m.member_code


class ApprovalSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    method = serializers.SerializerMethodField(
        help_text="device (fingerprint, face or phone PIN), sms, or null for a decline.")

    class Meta:
        model = Approval
        fields = ["name", "role", "decision", "reason", "method", "at"]

    def get_method(self, a) -> str | None:
        if a.decision != Approval.Decision.APPROVE:
            return None
        return "device" if a.device_id else "sms"

    def get_name(self, a) -> str:
        return _name(a.membership)


class ProposalSerializer(serializers.ModelSerializer):
    payee_member_code = serializers.CharField(source="payee_membership.member_code",
                                              default=None)
    proposed_by = serializers.SerializerMethodField()
    proposed_by_code = serializers.CharField(source="proposed_by.member_code")
    approvals = ApprovalSerializer(many=True, read_only=True)
    approved = serializers.SerializerMethodField()
    can_answer = serializers.SerializerMethodField()
    seal_receipt = serializers.CharField(source="entry.receipt_code", default=None)

    class Meta:
        model = Proposal
        fields = ["id", "kind", "state", "state_reason", "amount_cents", "payee_name",
                  "payee_member_code", "payee_account", "purpose", "proposed_by",
                  "proposed_by_code",
                  "needs_officials", "approved", "approvals", "refusal", "deadline_at",
                  "bank_reference", "paid_at", "can_answer", "seal_receipt", "created_at"]

    def get_proposed_by(self, p) -> str:
        return _name(p.proposed_by)

    def get_approved(self, p) -> int:
        return sum(1 for a in p.approvals.all() if a.decision == Approval.Decision.APPROVE)

    def get_can_answer(self, p) -> bool:
        """Whether the person asking may approve or decline it now."""
        me = self.context.get("me")
        if me is None or p.state != Proposal.State.COLLECTING or p.payee_membership_id == me.id:
            return False
        if any(a.membership_id == me.id for a in p.approvals.all()):
            return False
        return services.eligible_approvers(p).filter(pk=me.pk).exists()


class ProposeSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=Proposal.Kind.choices)
    amount_cents = serializers.IntegerField(min_value=1)
    purpose = serializers.CharField(max_length=200)
    payee_membership_id = serializers.UUIDField(required=False)
    payee_name = serializers.CharField(required=False, allow_blank=True, max_length=120)
    payee_account = serializers.CharField(required=False, allow_blank=True, max_length=120)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=300)


class PaidSerializer(serializers.Serializer):
    bank_reference = serializers.CharField(required=False, allow_blank=True, max_length=100)


class UnapprovedDebitSerializer(serializers.ModelSerializer):
    amount_cents = serializers.IntegerField(source="bank_txn.amount_cents")
    value_date = serializers.DateField(source="bank_txn.value_date")
    narration = serializers.CharField(source="bank_txn.narration")
    receipt = serializers.CharField(source="entry.receipt_code")
    resolved_by = serializers.SerializerMethodField()
    linked_proposal_id = serializers.UUIDField(allow_null=True)

    class Meta:
        model = UnapprovedDebit
        fields = ["id", "state", "amount_cents", "value_date", "narration", "receipt",
                  "explanation", "resolved_by", "resolved_at", "linked_proposal_id",
                  "alerted", "created_at"]

    def get_resolved_by(self, ud) -> str | None:
        return _name(ud.resolved_by) if ud.resolved_by_id else None


class LinkSerializer(serializers.Serializer):
    proposal_id = serializers.UUIDField()


class ExplainSerializer(serializers.Serializer):
    explanation = serializers.CharField(max_length=500)


class MoneyOutSerializer(serializers.Serializer):
    mode = serializers.CharField()
    consents = serializers.IntegerField()
    needed = serializers.IntegerField()
    available_cents = serializers.IntegerField(allow_null=True)


def _proposals():
    return Proposal.objects.select_related(
        "payee_membership", "proposed_by__user", "entry").prefetch_related(
        "approvals__membership__user")


def _proposal_for(request, pk) -> tuple[Proposal, Membership]:
    p = _proposals().select_related("chama").filter(pk=pk).first()
    if p is None:
        raise Problem("not_found", "Proposal not found.", status.HTTP_404_NOT_FOUND)
    return p, require_member(request.user, p.chama)


def _out(p: Proposal, me: Membership) -> dict:
    p = _proposals().get(pk=p.pk)
    return ProposalSerializer(p, context={"me": me}).data


class MoneyOutView(APIView):
    """Whether money out is on, and switching it on: two officials, each by SMS code."""

    def _state(self, chama):
        return {"mode": chama.mode,
                "consents": MoneyOutConsent.objects.filter(chama=chama).count(),
                "needed": services.CONSENTS_NEEDED,
                "available_cents": services.available_cents(chama)}

    @extend_schema(responses=MoneyOutSerializer)
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        return Response(self._state(chama))

    @extend_schema(request=ConfirmSerializer,
                   responses={200: MoneyOutSerializer, 202: ChallengeSentSerializer})
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        s = ConfirmSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if "code" not in s.validated_data:
            return code_sent(services.start_enable(chama, me), request.user)
        services.enable(chama, me, challenge_id=s.validated_data["challenge_id"],
                        sms_code=s.validated_data["code"])
        return Response(self._state(Chama.objects.get(pk=chama.pk)))


class ProposalListView(APIView):
    @extend_schema(responses=ProposalSerializer(many=True))
    def get(self, request, pk):
        chama, me = chama_for(request, pk)
        services.expire_due(chama)
        items = _proposals().filter(chama=chama).order_by("-created_at")
        return Response(ProposalSerializer(items, many=True, context={"me": me}).data)

    @extend_schema(request=ProposeSerializer, responses={201: ProposalSerializer})
    @idempotent()
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        s = ProposeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        payee = None
        if v.get("payee_membership_id"):
            payee = Membership.objects.select_related("user").filter(
                pk=v["payee_membership_id"], chama=chama).first()
        p = services.propose(chama, me, kind=v["kind"], amount_cents=v["amount_cents"],
                             purpose=v["purpose"], payee=payee,
                             payee_name=v.get("payee_name", ""),
                             payee_account=v.get("payee_account", ""))
        return Response(_out(p, me), status=status.HTTP_201_CREATED)


class ProposalDetailView(APIView):
    @extend_schema(responses=ProposalSerializer)
    def get(self, request, pk):
        p, me = _proposal_for(request, pk)
        services.expire_due(p.chama)
        return Response(_out(p, me))


class ProposalApproveView(APIView):
    @extend_schema(request=ConfirmSerializer,
                   responses={200: ProposalSerializer, 202: ChallengeSentSerializer})
    def post(self, request, pk):
        p, me = _proposal_for(request, pk)
        s = ConfirmSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if "code" not in s.validated_data:
            return code_sent(services.start_approve(p, me), request.user)
        p = services.approve(p, me, challenge_id=s.validated_data["challenge_id"],
                             sms_code=s.validated_data["code"])
        return Response(_out(p, me))


class ProposalDeclineView(APIView):
    @extend_schema(request=ReasonSerializer, responses=ProposalSerializer)
    def post(self, request, pk):
        p, me = _proposal_for(request, pk)
        s = ReasonSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return Response(_out(services.decline(p, me, s.validated_data.get("reason", "")), me))


class ProposalCancelView(APIView):
    @extend_schema(request=ReasonSerializer, responses=ProposalSerializer)
    def post(self, request, pk):
        p, me = _proposal_for(request, pk)
        s = ReasonSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return Response(_out(services.cancel(p, me, s.validated_data.get("reason", "")), me))


class ProposalPaidView(APIView):
    @extend_schema(request=PaidSerializer, responses=ProposalSerializer)
    @idempotent()
    def post(self, request, pk):
        p, me = _proposal_for(request, pk)
        s = PaidSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        p = services.mark_paid(p, me, s.validated_data.get("bank_reference", ""))
        return Response(_out(p, me))


class UnapprovedListView(APIView):
    @extend_schema(responses=UnapprovedDebitSerializer(many=True))
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        items = (UnapprovedDebit.objects.filter(chama=chama)
                 .select_related("bank_txn", "entry", "resolved_by__user").order_by("-created_at"))
        return Response(UnapprovedDebitSerializer(items, many=True).data)


def _ud_for(request, pk) -> tuple[UnapprovedDebit, Membership]:
    ud = UnapprovedDebit.objects.select_related("chama", "bank_txn", "entry").filter(
        pk=pk).first()
    if ud is None:
        raise Problem("not_found", "Not found.", status.HTTP_404_NOT_FOUND)
    return ud, require_member(request.user, ud.chama)


class UnapprovedLinkView(APIView):
    @extend_schema(request=LinkSerializer, responses=UnapprovedDebitSerializer)
    def post(self, request, pk):
        ud, me = _ud_for(request, pk)
        s = LinkSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        p = Proposal.objects.filter(pk=s.validated_data["proposal_id"], chama=ud.chama).first()
        if p is None:
            raise Problem("not_found", "Proposal not found.", status.HTTP_404_NOT_FOUND)
        return Response(UnapprovedDebitSerializer(services.link(ud, p, me)).data)


class UnapprovedExplainView(APIView):
    @extend_schema(request=ExplainSerializer, responses=UnapprovedDebitSerializer)
    def post(self, request, pk):
        ud, me = _ud_for(request, pk)
        s = ExplainSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        return Response(UnapprovedDebitSerializer(
            services.explain(ud, me, s.validated_data["explanation"])).data)


class RotationStartSerializer(serializers.Serializer):
    order = serializers.ListField(child=serializers.UUIDField(), required=False,
                                  help_text="Membership ids, for a fixed order.")


class RotationTurnSerializer(serializers.ModelSerializer):
    member_code = serializers.CharField(source="membership.member_code")
    name = serializers.SerializerMethodField()
    membership_id = serializers.UUIDField()
    proposal_id = serializers.UUIDField(allow_null=True)
    proposal_state = serializers.CharField(source="proposal.state", default=None)

    class Meta:
        model = RotationTurn
        fields = ["position", "membership_id", "member_code", "name", "state", "note",
                  "proposal_id", "proposal_state"]

    def get_name(self, t) -> str:
        return _name(t.membership)


class RotationSerializer(serializers.Serializer):
    settings = serializers.JSONField(allow_null=True)
    cycle_no = serializers.IntegerField(allow_null=True)
    method = serializers.CharField(allow_null=True)
    seed = serializers.CharField(allow_null=True)
    receipt = serializers.CharField(allow_null=True)
    running = serializers.BooleanField()
    pot_cents = serializers.IntegerField(allow_null=True)
    next_position = serializers.IntegerField(allow_null=True)
    turns = RotationTurnSerializer(many=True)


def _rotation_data(chama) -> dict:
    r = rot.latest(chama)
    running = r is not None and rot.current(chama) is not None
    turns = (r.turns.select_related("membership__user", "proposal").order_by("position")
             if r else [])
    nxt = rot.next_turn(r) if running else None
    return {
        "settings": rot.settings_for(chama),
        "cycle_no": r.cycle_no if r else None,
        "method": r.method if r else None,
        "seed": r.seed if r else None,
        "receipt": r.entry.receipt_code if r else None,
        "running": running,
        "pot_cents": rot.pot_cents(r) if r else None,
        "next_position": nxt.position if nxt else None,
        "turns": RotationTurnSerializer(turns, many=True).data,
    }


class RotationView(APIView):
    """The merry-go-round: whose turn is next, and starting a cycle."""

    @extend_schema(responses=RotationSerializer)
    def get(self, request, pk):
        chama, _ = chama_for(request, pk)
        services.expire_due(chama)
        return Response(_rotation_data(chama))

    @extend_schema(request=RotationStartSerializer, responses={201: RotationSerializer})
    @idempotent()
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        s = RotationStartSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        rot.start(chama, me, order_ids=s.validated_data.get("order"))
        return Response(_rotation_data(chama), status=status.HTTP_201_CREATED)


class RotationNextView(APIView):
    """Propose the next turn's payout, filled in from the rotation and the rules."""

    @extend_schema(request=None, responses={201: ProposalSerializer})
    @idempotent()
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        services.expire_due(chama)
        p = rot.propose_next(chama, me)
        return Response(_out(p, me), status=status.HTTP_201_CREATED)


class SigningPayloadSerializer(serializers.Serializer):
    device_id = serializers.UUIDField()
    payload = serializers.JSONField()
    payload_hash = serializers.CharField()


class SignatureSerializer(serializers.Serializer):
    device_id = serializers.UUIDField()
    nonce = serializers.CharField(max_length=64)
    payload_hash = serializers.CharField(max_length=64)
    signature = serializers.CharField(help_text="Base64 DER ECDSA signature.")


class ProposalSigningPayloadView(APIView):
    """What the phone shows and signs, with a fresh single-use nonce."""

    @extend_schema(request=None, responses=SigningPayloadSerializer)
    def post(self, request, pk):
        p, me = _proposal_for(request, pk)
        return Response(services.start_device_approve(p, me))


class ProposalSignatureView(APIView):
    """An approval signed by the phone's key after the official's fingerprint, face or
    phone PIN. A payload changed since it was shown returns 409."""

    @extend_schema(request=SignatureSerializer, responses=ProposalSerializer)
    def post(self, request, pk):
        p, me = _proposal_for(request, pk)
        s = SignatureSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        p = services.approve_with_device(p, me, device_id=v["device_id"], nonce=v["nonce"],
                                         payload_hash=v["payload_hash"],
                                         signature_b64=v["signature"])
        return Response(_out(p, me))
