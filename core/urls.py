from django.contrib.auth import views as auth_views
from django.urls import path

from core import views

app_name = "core"

urlpatterns = [
    path("", views.DayView.as_view(), name="day"),
    path("day/<str:date>/", views.DayView.as_view(), name="day_on"),
    path("day/<str:date>/entries/new/", views.EntryCreateView.as_view(), name="entry_create"),
    path("entries/<int:pk>/edit/", views.EntryUpdateView.as_view(), name="entry_edit"),
    path("entries/<int:pk>/delete/", views.EntryDeleteView.as_view(), name="entry_delete"),
    path("target/", views.DailyTargetUpdateView.as_view(), name="daily_target_edit"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("healthz", views.HealthzView.as_view(), name="healthz"),
    path("service-worker.js", views.ServiceWorkerView.as_view(), name="service_worker"),
    path("products/", views.ProductListView.as_view(), name="product_list"),
    path("products/new/", views.ProductCreateView.as_view(), name="product_create"),
    path("products/<int:pk>/edit/", views.ProductUpdateView.as_view(), name="product_edit"),
    path("products/<int:pk>/delete/", views.ProductDeleteView.as_view(), name="product_delete"),
]
