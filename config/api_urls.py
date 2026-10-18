from django.urls import include, path

from apps.chamas import views as chamas
from apps.contributions import views as contrib
from apps.feeds import views as feeds
from apps.ledger import views as ledger
from apps.reconciliation import views as recon

urlpatterns = [
    path("auth/", include("apps.accounts.urls")),
    path("platform-key/", ledger.PlatformKeyView.as_view()),
    path("rule-templates/", chamas.RuleTemplatesView.as_view()),
    path("chamas/", chamas.ChamaListView.as_view()),
    path("chamas/<uuid:pk>/", chamas.ChamaDetailView.as_view()),
    path("chamas/<uuid:pk>/bank-account/", chamas.BankAccountView.as_view()),
    path("chamas/<uuid:pk>/rules/", chamas.RulesView.as_view()),
    path("chamas/<uuid:pk>/invites/", chamas.InviteCreateView.as_view()),
    path("chamas/<uuid:pk>/submit/", chamas.SubmitView.as_view()),
    path("chamas/<uuid:pk>/adopt/", chamas.AdoptView.as_view()),
    path("chamas/<uuid:pk>/members/", chamas.MembersView.as_view()),
    path("chamas/<uuid:pk>/statements/", feeds.StatementListView.as_view()),
    path("chamas/<uuid:pk>/unmatched/", recon.UnmatchedView.as_view()),
    path("chamas/<uuid:pk>/ledger/", ledger.LedgerView.as_view()),
    path("chamas/<uuid:pk>/ledger/head/", ledger.LedgerHeadView.as_view()),
    path("invites/<str:code>/", chamas.InviteAcceptView.as_view()),
    path("memberships/<uuid:pk>/position/", chamas.PositionView.as_view()),
    path("statements/<uuid:pk>/", feeds.StatementDetailView.as_view()),
    path("statements/<uuid:pk>/confirm/", feeds.StatementConfirmView.as_view()),
    path("bank-transactions/<uuid:pk>/assign/", recon.AssignView.as_view()),
    path("bank-transactions/<uuid:pk>/not-me/", recon.NotMeView.as_view()),
    path("hooks/mail/<str:token>/inbound/", feeds.MailInboundHook.as_view()),
    path("chamas/<uuid:pk>/opening-balances/", contrib.OpeningView.as_view()),
    path("chamas/<uuid:pk>/opening-balances/confirm/", contrib.OpeningConfirmView.as_view()),
    path("opening-balance-lines/<uuid:pk>/object/", contrib.OpeningObjectView.as_view()),
    path("chamas/<uuid:pk>/cash-receipts/", contrib.CashListView.as_view()),
    path("cash-receipts/<uuid:pk>/confirm/", contrib.CashConfirmView.as_view()),
    path("cash-receipts/<uuid:pk>/decline/", contrib.CashDeclineView.as_view()),
    path("bank-transactions/<uuid:pk>/bank-cash/", contrib.BankCashView.as_view()),
]
