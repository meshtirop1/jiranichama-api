from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

urlpatterns = [
    path("otp/", views.RequestCodeView.as_view(), name="auth-otp"),
    path("verify/", views.VerifyCodeView.as_view(), name="auth-verify"),
    path("pin/", views.SetPinView.as_view(), name="auth-pin"),
    path("refresh/", TokenRefreshView.as_view(), name="auth-refresh"),
    path("logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("me/", views.ProfileView.as_view(), name="auth-me"),
]
