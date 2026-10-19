"""Meetings in the app (5.11)."""

from django.http import FileResponse
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.chamas.access import require_member
from apps.chamas.models import Membership
from apps.chamas.views import chama_for
from apps.core.idempotency import idempotent
from apps.core.problems import Problem

from . import services
from .models import Meeting, Minutes


def _name(m: Membership) -> str:
    return m.user.display_name or m.member_code


class MinutesObjectionOutSerializer(serializers.Serializer):
    by = serializers.CharField()
    reason = serializers.CharField()
    at = serializers.DateTimeField()


class MinutesSerializer(serializers.ModelSerializer):
    written_by = serializers.SerializerMethodField()
    has_photo = serializers.SerializerMethodField()
    objections = serializers.SerializerMethodField()

    class Meta:
        model = Minutes
        fields = ["id", "version", "text", "has_photo", "photo_sha256", "hash", "written_by",
                  "objection_until", "objections", "created_at"]

    def get_written_by(self, m) -> str:
        return _name(m.written_by)

    def get_has_photo(self, m) -> bool:
        return bool(m.photo)

    @extend_schema(responses=MinutesObjectionOutSerializer(many=True))
    def get_objections(self, m) -> list[dict]:
        return [{"by": _name(o.membership), "reason": o.reason, "at": o.created_at}
                for o in m.objections.select_related("membership__user").order_by("created_at")]


class MeetingPersonSerializer(serializers.Serializer):
    membership_id = serializers.UUIDField()
    name = serializers.CharField()
    member_code = serializers.CharField()
    status = serializers.ChoiceField(choices=["present", "late", "excused", "apology",
                                              "absent", "expected"])
    reason = serializers.CharField(allow_null=True)


class MeetingSerializer(serializers.ModelSerializer):
    i_run = serializers.SerializerMethodField()
    my_status = serializers.SerializerMethodField()
    people = serializers.SerializerMethodField(
        help_text="Every member expected, with where they stand.")
    minutes = serializers.SerializerMethodField()
    attendance_receipt = serializers.CharField(source="closed_entry.receipt_code",
                                               default=None)
    sealed_receipt = serializers.CharField(source="sealed_entry.receipt_code", default=None)

    class Meta:
        model = Meeting
        fields = ["id", "title", "starts_at", "place", "online_link", "agenda", "state",
                  "cancel_reason", "i_run", "my_status", "people", "minutes",
                  "attendance_receipt", "sealed_receipt", "created_at"]

    def _me(self):
        return self.context.get("me")

    def get_i_run(self, mt) -> bool:
        me = self._me()
        return bool(me) and services.runs_meetings(me)

    def _people(self, mt) -> list[dict]:
        if "people" not in self.context:
            att = {a.membership_id: a for a in mt.attendance.all()}
            apol = {a.membership_id: a for a in mt.apologies.all()}
            _, s = services._rules_on(mt)
            closed = mt.state not in (Meeting.State.SCHEDULED, Meeting.State.IN_PROGRESS)
            out = []
            for m in services.expected(mt):
                a, ap = att.get(m.id), apol.get(m.id)
                if a is not None:
                    st = "late" if a.late else "present"
                elif ap is not None:
                    st = "excused" if s["apology_excuses"] else "apology"
                    if not closed:
                        st = "apology"
                else:
                    st = "absent" if closed else "expected"
                out.append({"membership_id": m.id, "name": _name(m),
                            "member_code": m.member_code, "status": st,
                            "reason": ap.reason if ap else None})
            self.context["people"] = out
        return self.context["people"]

    @extend_schema(responses=MeetingPersonSerializer(many=True))
    def get_people(self, mt) -> list[dict]:
        self.context.pop("people", None)
        return MeetingPersonSerializer(self._people(mt), many=True).data

    def get_my_status(self, mt) -> str | None:
        me = self._me()
        if not me:
            return None
        return next((p["status"] for p in self._people(mt) if p["membership_id"] == me.id),
                    None)

    @extend_schema(responses=MinutesSerializer(many=True))
    def get_minutes(self, mt) -> list[dict]:
        return MinutesSerializer(mt.minutes.select_related("written_by__user")
                                 .order_by("-version"), many=True).data


def _meetings():
    return Meeting.objects.select_related("chama", "closed_entry", "sealed_entry")


def _data(meeting, me):
    return MeetingSerializer(_meetings().get(pk=meeting.pk), context={"me": me}).data


def _meeting_for(request, pk):
    mt = _meetings().filter(pk=pk).first()
    if mt is None:
        raise Problem("not_found", "Meeting not found.", status.HTTP_404_NOT_FOUND)
    return mt, require_member(request.user, mt.chama)


class ScheduleSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=120)
    starts_at = serializers.DateTimeField()
    place = serializers.CharField(max_length=200, required=False, allow_blank=True,
                                  default="")
    online_link = serializers.URLField(required=False, allow_blank=True, default="")
    agenda = serializers.CharField(required=False, allow_blank=True, default="")


class MeetingReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


class MeetingApologySerializer(MeetingReasonSerializer):
    membership_id = serializers.UUIDField(required=False,
                                          help_text="Secretary only: for another member.")


class CheckInSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=12)


class MarkSerializer(serializers.Serializer):
    membership_id = serializers.UUIDField()
    late = serializers.BooleanField(required=False, allow_null=True, default=None)


class CheckInCodeSerializer(serializers.Serializer):
    code = serializers.CharField()
    qr = serializers.CharField()
    changes_in_seconds = serializers.IntegerField()


class MinutesInSerializer(serializers.Serializer):
    text = serializers.CharField(required=False, allow_blank=True, default="")
    photo = serializers.FileField(required=False)


def _member(meeting, pk) -> Membership:
    m = Membership.objects.filter(chama=meeting.chama, pk=pk).first()
    if m is None:
        raise Problem("not_found", "Member not found.", status.HTTP_404_NOT_FOUND)
    return m


class MeetingListView(APIView):
    @extend_schema(responses=MeetingSerializer(many=True))
    def get(self, request, pk):
        chama, me = chama_for(request, pk)
        qs = _meetings().filter(chama=chama).order_by("-starts_at")[:30]
        return Response(MeetingSerializer(qs, many=True, context={"me": me}).data)

    @extend_schema(request=ScheduleSerializer, responses={201: MeetingSerializer})
    @idempotent()
    def post(self, request, pk):
        chama, me = chama_for(request, pk)
        s = ScheduleSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        mt = services.schedule(chama, me, **s.validated_data)
        return Response(_data(mt, me), status=status.HTTP_201_CREATED)


class MeetingDetailView(APIView):
    @extend_schema(responses=MeetingSerializer)
    def get(self, request, pk):
        mt, me = _meeting_for(request, pk)
        return Response(_data(mt, me))


class MeetingCancelView(APIView):
    @extend_schema(request=MeetingReasonSerializer, responses=MeetingSerializer)
    def post(self, request, pk):
        mt, me = _meeting_for(request, pk)
        s = MeetingReasonSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.cancel(mt, me, reason=s.validated_data["reason"])
        return Response(_data(mt, me))


class ApologyView(APIView):
    @extend_schema(request=MeetingApologySerializer, responses=MeetingSerializer)
    def post(self, request, pk):
        mt, me = _meeting_for(request, pk)
        s = MeetingApologySerializer(data=request.data)
        s.is_valid(raise_exception=True)
        member = (_member(mt, s.validated_data["membership_id"])
                  if s.validated_data.get("membership_id") else None)
        services.apologise(mt, me, reason=s.validated_data["reason"], member=member)
        return Response(_data(mt, me))


class MeetingStartView(APIView):
    @extend_schema(request=None, responses=MeetingSerializer)
    def post(self, request, pk):
        mt, me = _meeting_for(request, pk)
        services.start(mt, me)
        return Response(_data(mt, me))


class CheckInCodeView(APIView):
    @extend_schema(responses=CheckInCodeSerializer)
    def get(self, request, pk):
        mt, me = _meeting_for(request, pk)
        return Response(services.check_in_code(mt, me))


class CheckInView(APIView):
    @extend_schema(request=CheckInSerializer, responses=MeetingSerializer)
    def post(self, request, pk):
        mt, me = _meeting_for(request, pk)
        s = CheckInSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.check_in(mt, me, code=s.validated_data["code"])
        return Response(_data(mt, me))


class MarkView(APIView):
    @extend_schema(request=MarkSerializer, responses=MeetingSerializer)
    def post(self, request, pk):
        mt, me = _meeting_for(request, pk)
        s = MarkSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.mark(mt, me, _member(mt, s.validated_data["membership_id"]),
                      late=s.validated_data["late"])
        return Response(_data(mt, me))


class CloseAttendanceView(APIView):
    @extend_schema(request=None, responses=MeetingSerializer)
    def post(self, request, pk):
        mt, me = _meeting_for(request, pk)
        services.close_attendance(mt, me)
        return Response(_data(mt, me))


class MinutesView(APIView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(request=MinutesInSerializer, responses={201: MeetingSerializer})
    def post(self, request, pk):
        mt, me = _meeting_for(request, pk)
        s = MinutesInSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.post_minutes(mt, me, text=s.validated_data["text"],
                              photo=s.validated_data.get("photo"))
        return Response(_data(mt, me), status=status.HTTP_201_CREATED)


def _minutes_for(request, pk):
    mn = Minutes.objects.select_related("meeting__chama").filter(pk=pk).first()
    if mn is None:
        raise Problem("not_found", "Minutes not found.", status.HTTP_404_NOT_FOUND)
    return mn, require_member(request.user, mn.meeting.chama)


class MinutesObjectionView(APIView):
    @extend_schema(request=MeetingReasonSerializer, responses=MeetingSerializer)
    def post(self, request, pk):
        mn, me = _minutes_for(request, pk)
        s = MeetingReasonSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.object_to(mn, me, reason=s.validated_data["reason"])
        return Response(_data(mn.meeting, me))


class MinutesPhotoView(APIView):
    @extend_schema(responses={(200, "image/jpeg"): bytes})
    def get(self, request, pk):
        mn, _ = _minutes_for(request, pk)
        if not mn.photo:
            raise Problem("not_found", "These minutes have no photo.",
                          status.HTTP_404_NOT_FOUND)
        return FileResponse(mn.photo.open("rb"), content_type="image/jpeg")
