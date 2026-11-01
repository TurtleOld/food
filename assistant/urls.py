from django.urls import path

from assistant import views

app_name = "assistant"

urlpatterns = [
    path("", views.BudgetView.as_view(), name="budget"),
    path("settings/", views.SettingsView.as_view(), name="settings"),
    path("proposals/", views.ProposalListView.as_view(), name="proposals"),
    path(
        "proposals/<int:pk>/edit/",
        views.ProposalUpdateView.as_view(),
        name="proposal_edit",
    ),
    path(
        "proposals/<int:pk>/confirm/",
        views.ProposalConfirmView.as_view(),
        name="proposal_confirm",
    ),
    path(
        "proposals/<int:pk>/reject/",
        views.ProposalRejectView.as_view(),
        name="proposal_reject",
    ),
]
