from __future__ import annotations

import datetime
import hashlib
import json
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import ProtectedError, QuerySet
from django.forms import Form, ModelForm
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, ListView, TemplateView, UpdateView

from core.diary import day_summary
from core.forms import (
    DailyTargetForm,
    DiaryEntryEditForm,
    DiaryEntryForm,
    ProductCreateForm,
    ProductEditForm,
)
from core.models import DailyTarget, DiaryEntry, Product


def _parse_date(value: str) -> datetime.date:
    """Parse a URL date segment, raising Http404 on a malformed value."""
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise Http404("Неверная дата") from None


def _day_url(date: datetime.date) -> str:
    """Build the URL of the day page for the given date."""
    return reverse("core:day_on", args=[date.isoformat()])


class DayView(LoginRequiredMixin, TemplateView):
    """Личная страница дня участника с итогами по Приёмам пищи."""

    template_name = "core/day.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        date = self.kwargs.get("date")
        current_date = _parse_date(date) if date is not None else datetime.date.today()
        summary = day_summary(self.request.user, current_date)
        context.update(
            current_date=current_date,
            previous_date=current_date - datetime.timedelta(days=1),
            next_date=current_date + datetime.timedelta(days=1),
            meals=summary.meals,
            totals=summary.totals,
            progress=summary.progress,
        )
        return context


class OwnEntryMixin(LoginRequiredMixin):
    """Ограничивает записи дневника записями текущего участника."""

    request: HttpRequest
    model = DiaryEntry

    def get_queryset(self) -> QuerySet[DiaryEntry]:
        return DiaryEntry.objects.filter(member_id=self.request.user.pk)


class EntryCreateView(LoginRequiredMixin, CreateView):
    """Создаёт запись дневника текущего участника на заданный день."""

    form_class = DiaryEntryForm
    template_name = "core/entry_form.html"

    def setup(self, request: HttpRequest, *args: Any, **kwargs: Any) -> None:
        super().setup(request, *args, **kwargs)
        self.entry_date = _parse_date(kwargs["date"])

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        form.instance.member = self.request.user
        form.instance.date = self.entry_date
        messages.success(self.request, "Запись добавлена")
        return super().form_valid(form)

    def get_success_url(self) -> str:
        return _day_url(self.entry_date)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.update(date=self.entry_date, cancel_url=_day_url(self.entry_date))
        return context


class EntryUpdateView(OwnEntryMixin, UpdateView):
    """Редактирует запись дневника текущего участника."""

    form_class = DiaryEntryEditForm
    template_name = "core/entry_form.html"
    context_object_name = "entry"

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        messages.success(self.request, "Запись обновлена")
        return super().form_valid(form)

    def get_success_url(self) -> str:
        return _day_url(self.object.date)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["cancel_url"] = _day_url(self.object.date)
        return context


class EntryDeleteView(OwnEntryMixin, DeleteView):
    """Удаляет запись дневника текущего участника после подтверждения."""

    template_name = "core/entry_confirm_delete.html"
    context_object_name = "entry"

    def form_valid(self, form: Form) -> HttpResponse:
        messages.success(self.request, "Запись удалена")
        return super().form_valid(form)

    def get_success_url(self) -> str:
        return _day_url(self.object.date)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["cancel_url"] = _day_url(self.object.date)
        return context


class DailyTargetUpdateView(LoginRequiredMixin, UpdateView):
    """Редактирует собственную Суточную цель участника, создавая её при первом сохранении."""

    form_class = DailyTargetForm
    template_name = "core/daily_target_form.html"
    success_url = reverse_lazy("core:day")

    def get_object(self, queryset: QuerySet[DailyTarget] | None = None) -> DailyTarget | None:
        return DailyTarget.objects.filter(member_id=self.request.user.pk).first()

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        form.instance.member = self.request.user
        messages.success(self.request, "Цель сохранена")
        return super().form_valid(form)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["cancel_url"] = _day_url(datetime.date.today())
        return context


class HealthzView(View):
    """Сообщает, что процесс приложения жив."""

    def get(self, request: HttpRequest) -> HttpResponse:
        return HttpResponse("ok")


PRECACHE_STATIC_FILES = (
    "vendor/htmx/htmx-2.0.11.min.js",
    "vendor/alpine/alpine-3.17.4.min.js",
    "vendor/bulma/bulma-1.0.4.min.css",
    "css/app.css",
    "manifest.webmanifest",
    "icons/icon-192.png",
    "icons/icon-512.png",
    "icons/icon-maskable-512.png",
    "icons/apple-touch-icon.png",
    "icons/favicon.svg",
    "offline.html",
)


class ServiceWorkerView(View):
    """Отдаёт service worker с корня origin, чтобы его scope покрывал всё приложение.

    Скрипт рендерится шаблоном: список precache состоит из хэшированных URL
    статики, а имя кэша выводится из него, поэтому новая статика даёт новые
    байты воркера и браузер ставит свежую версию.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        precache_urls = [static(name) for name in PRECACHE_STATIC_FILES]
        cache_hash = hashlib.sha256("\n".join(precache_urls).encode()).hexdigest()[:12]
        response = render(
            request,
            "service-worker.js",
            {
                "precache_json": json.dumps(precache_urls),
                "cache_name": f"food-static-{cache_hash}",
                "offline_url": static("offline.html"),
                "static_prefix": static(""),
                "bypass_cache": settings.DEBUG,
            },
            content_type="application/javascript",
        )
        response["Service-Worker-Allowed"] = "/"
        response["Cache-Control"] = "no-cache"
        return response


class ProductListView(LoginRequiredMixin, ListView):
    """Общий Каталог продуктов с фильтром по названию."""

    template_name = "core/product_list.html"
    context_object_name = "products"

    def get_queryset(self) -> QuerySet[Product]:
        products = Product.objects.select_related("author")
        query = self.query()
        if not query:
            return products
        needle = query.lower()
        # SQLite lower()/icontains не сворачивают регистр кириллицы, поэтому фильтруем в Python.
        matching_ids = [product.pk for product in products if needle in product.name.lower()]
        return products.filter(pk__in=matching_ids)

    def query(self) -> str:
        return self.request.GET.get("q", "").strip()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["query"] = self.query()
        return context


class ProductCreateView(LoginRequiredMixin, CreateView):
    """Создаёт продукт Каталога с автором из текущего участника."""

    form_class = ProductCreateForm
    template_name = "core/product_form.html"
    success_url = reverse_lazy("core:product_list")

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        form.instance.author = self.request.user
        messages.success(self.request, "Продукт создан")
        return super().form_valid(form)


class ProductUpdateView(LoginRequiredMixin, UpdateView):
    """Редактирует любой продукт Каталога; базовая единица остаётся прежней."""

    model = Product
    form_class = ProductEditForm
    template_name = "core/product_form.html"
    context_object_name = "product"
    success_url = reverse_lazy("core:product_list")

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        messages.success(self.request, "Продукт обновлён")
        return super().form_valid(form)


class ProductDeleteView(LoginRequiredMixin, DeleteView):
    """Удаляет продукт Каталога, если на него не ссылаются записи дневника."""

    model = Product
    template_name = "core/product_confirm_delete.html"
    context_object_name = "product"
    success_url = reverse_lazy("core:product_list")

    def form_valid(self, form: Form) -> HttpResponse:
        try:
            response = super().form_valid(form)
        except ProtectedError:
            messages.error(
                self.request,
                "Продукт нельзя удалить: на него ссылаются записи дневника",
            )
            return redirect(self.success_url)
        messages.success(self.request, "Продукт удалён")
        return response
