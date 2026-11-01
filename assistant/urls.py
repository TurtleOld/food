from django.urls import path

from assistant import views

app_name = "assistant"

urlpatterns = [
    path("", views.BudgetView.as_view(), name="budget"),
    path("settings/", views.SettingsView.as_view(), name="settings"),
]
