from django.contrib.auth import views as auth_views
from django.urls import path

from core import views

app_name = "core"

urlpatterns = [
    path("", views.day, name="day"),
    path("day/<str:date>/", views.day, name="day_on"),
    path("day/<str:date>/entries/new/", views.entry_create, name="entry_create"),
    path("entries/<int:pk>/edit/", views.entry_edit, name="entry_edit"),
    path("entries/<int:pk>/delete/", views.entry_delete, name="entry_delete"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("healthz", views.healthz, name="healthz"),
    path("products/", views.product_list, name="product_list"),
    path("products/new/", views.product_create, name="product_create"),
    path("products/<int:pk>/edit/", views.product_edit, name="product_edit"),
    path("products/<int:pk>/delete/", views.product_delete, name="product_delete"),
]
