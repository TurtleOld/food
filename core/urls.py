from django.contrib.auth import views as auth_views
from django.urls import path

from core import views

app_name = "core"

urlpatterns = [
    path("", views.day, name="day"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("healthz", views.healthz, name="healthz"),
]
