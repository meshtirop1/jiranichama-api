from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.problems import Problem

from . import services
from .phones import phone_hint
from .serializers import (
    LogoutSerializer,
    ProfileSerializer,
    RequestCodeResponseSerializer,
    RequestCodeSerializer,
    SetPinSerializer,
    SignInResponseSerializer,
    SignInUserSerializer,
    VerifyCodeSerializer,
)


class RequestCodeView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp"

    @extend_schema(request=RequestCodeSerializer, responses={202: RequestCodeResponseSerializer})
    def post(self, request):
        s = RequestCodeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        phone = s.validated_data["phone"]
        services.request_code(phone, s.validated_data["language"])
        return Response(
            {"phone_hint": phone_hint(phone), "expires_in": settings.OTP_TTL_SECONDS},
            status=status.HTTP_202_ACCEPTED,
        )


class VerifyCodeView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "otp_verify"

    @extend_schema(request=VerifyCodeSerializer, responses={200: SignInResponseSerializer})
    def post(self, request):
        s = VerifyCodeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        result = services.verify_code(s.validated_data["phone"], s.validated_data["code"])
        return Response(
            {
                "access": result.access,
                "refresh": result.refresh,
                "is_new": result.is_new,
                "user": SignInUserSerializer(result.user).data,
            }
        )


class SetPinView(APIView):
    @extend_schema(request=SetPinSerializer, responses={204: None})
    def post(self, request):
        s = SetPinSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        services.set_pin(request.user, s.validated_data["pin"],
                         s.validated_data.get("current_pin"))
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProfileView(APIView):
    """The signed-in person's name and language. Members see each other by name in their
    chamas, so a name is asked for once, at sign-up."""

    @extend_schema(responses=ProfileSerializer)
    def get(self, request):
        return Response(ProfileSerializer(request.user).data)

    @extend_schema(request=ProfileSerializer, responses=ProfileSerializer)
    def patch(self, request):
        s = ProfileSerializer(data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        user = request.user
        for field in ("display_name", "language"):
            if field in s.validated_data:
                setattr(user, field, s.validated_data[field])
        user.save(update_fields=["display_name", "language"])
        return Response(ProfileSerializer(user).data)


class LogoutView(APIView):
    @extend_schema(request=LogoutSerializer, responses={204: None})
    def post(self, request):
        s = LogoutSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            token = RefreshToken(s.validated_data["refresh"])
        except TokenError as exc:
            raise Problem("token_invalid", "This session has already ended.") from exc
        if str(token.get("user_id")) != str(request.user.id):
            raise Problem("token_invalid", "This session does not belong to you.")
        token.blacklist()
        return Response(status=status.HTTP_204_NO_CONTENT)
