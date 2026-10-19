from django.urls import path

from . import views

app_name = "console"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("queues/statements/", views.statements, name="statements"),
    path("queues/integrity/", views.integrity, name="integrity"),
    path("queues/support/", views.support_queue, name="support"),
    path("queues/referrals/", views.referrals, name="referrals"),
    path("chamas/", views.chamas, name="chamas"),
    path("chamas/<uuid:pk>/", views.chama, name="chama"),
    path("flags/", views.flags, name="flags"),
    path("flags/<slug:key>/", views.flag, name="flag"),
    path("feeds/", views.feeds, name="feeds"),
    path("partners/", views.partners, name="partners"),
    path("audit/", views.audit_log, name="audit"),
]
