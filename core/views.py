from __future__ import annotations

import copy
import datetime
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.contrib.staticfiles import finders
from django.db.models import ProtectedError, QuerySet
from django.forms import Form, ModelForm
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
)

from core.diary import (
    day_summary,
    deletion_token,
    edit_token,
    last_amount,
    meal_slots,
    rings,
)
from core.forms import (
    DailyTargetForm,
    DiaryEntryEditForm,
    DiaryEntryForm,
    ProductCreateForm,
    ProductEditForm,
)
from core.htmx import (
    FragmentMixin,
    HtmxLoginRequiredMixin,
    sheet_saved_response,
    with_toasts,
)
from core.models import DailyTarget, DiaryEntry, Product
from core.undo import add_undo_message, restore

QUICK_AMOUNTS = (50, 100, 150, 200)


def _parse_date(value: str) -> datetime.date:
    """Parse a URL date segment, raising Http404 on a malformed value."""
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise Http404("Неверная дата") from None


def _day_url(date: datetime.date) -> str:
    """Build the URL of the day page for the given date."""
    return reverse("core:day_on", args=[date.isoformat()])


def _day_context(
    member: AbstractBaseUser | AnonymousUser, current_date: datetime.date
) -> dict[str, Any]:
    """Собирает контекст страницы дня и её фрагмента `feed`."""
    summary = day_summary(member, current_date)
    return {
        "current_date": current_date,
        "previous_date": current_date - datetime.timedelta(days=1),
        "next_date": current_date + datetime.timedelta(days=1),
        "today": datetime.date.today(),
        "meals": summary.meals,
        "slots": meal_slots(summary.meals),
        "rings": rings(summary.totals, summary.progress),
        "totals": summary.totals,
        "progress": summary.progress,
    }


class DayView(HtmxLoginRequiredMixin, FragmentMixin, TemplateView):
    """Личная страница дня участника с итогами по Приёмам пищи."""

    template_name = "core/day.html"
    fragment_template_name = "core/day.html#feed"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        date = self.kwargs.get("date")
        current_date = _parse_date(date) if date is not None else datetime.date.today()
        context.update(_day_context(self.request.user, current_date))
        return context


class OwnEntryMixin(HtmxLoginRequiredMixin):
    """Ограничивает записи дневника записями текущего участника."""

    request: HttpRequest
    model = DiaryEntry

    def get_queryset(self) -> QuerySet[DiaryEntry]:
        return DiaryEntry.objects.filter(member_id=self.request.user.pk)


class EntryCreateView(HtmxLoginRequiredMixin, CreateView):
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


class EntryUpdateView(OwnEntryMixin, FragmentMixin, UpdateView):
    """Редактирует запись дневника текущего участника."""

    form_class = DiaryEntryEditForm
    template_name = "core/entry_form.html"
    fragment_template_name = "core/entry_form.html#form"
    context_object_name = "entry"

    def get_object(self, queryset: QuerySet[DiaryEntry] | None = None) -> DiaryEntry:
        entry = super().get_object(queryset)
        # Форма меняет instance при валидации; лента после сохранения — того дня, откуда открыли.
        self.opened_from_date = entry.date
        self.undo_token = edit_token(entry)
        return entry

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        add_undo_message(self.request, "Запись обновлена", self.undo_token)
        response = super().form_valid(form)
        if not self.request.htmx:  # type: ignore[attr-defined]
            return response
        context = _day_context(self.request.user, self.opened_from_date)
        return sheet_saved_response(self.request, "core/day.html#feed", context, "#feed")

    def get_success_url(self) -> str:
        return _day_url(self.object.date)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        entry = self.object
        context["cancel_url"] = _day_url(entry.date)
        context["last_amount"] = last_amount(self.request.user, entry.product, exclude=entry)
        context["quick_amounts"] = QUICK_AMOUNTS
        context["macros"] = entry
        return context


class EntryPreviewView(OwnEntryMixin, DetailView):
    """Живой пересчёт КБЖУ записи для введённого количества по её снапшоту."""

    template_name = "core/entry_form.html#macros"
    context_object_name = "entry"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        form = DiaryEntryEditForm(
            {
                "date": self.object.date,
                "meal_type": self.object.meal_type,
                "amount": self.request.GET.get("amount", ""),
            },
            instance=copy.copy(self.object),
        )
        # Валидный instance получает введённое количество; формула та же, что у записи.
        context["macros"] = form.instance if form.is_valid() else None
        return context


class EntryDeleteView(OwnEntryMixin, DeleteView):
    """Удаляет запись дневника текущего участника.

    Из шторки (htmx) удаляет сразу и предлагает «Вернуть»; без JS — через страницу подтверждения.
    """

    template_name = "core/entry_confirm_delete.html"
    context_object_name = "entry"

    def form_valid(self, form: Form) -> HttpResponse:
        entry = self.object
        if not self.request.htmx:  # type: ignore[attr-defined]
            messages.success(self.request, "Запись удалена")
            return super().form_valid(form)
        token = deletion_token(entry)
        entry.delete()
        add_undo_message(self.request, "Запись удалена", token)
        context = _day_context(self.request.user, entry.date)
        return sheet_saved_response(self.request, "core/day.html#feed", context, "#feed")

    def get_success_url(self) -> str:
        return _day_url(self.object.date)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["cancel_url"] = _day_url(self.object.date)
        return context


class UndoView(HtmxLoginRequiredMixin, View):
    """Применяет подписанный снимок из тоста «Вернуть»."""

    def post(self, request: HttpRequest) -> HttpResponse:
        restored = restore(request.user, request.POST.get("token", ""))
        if restored is None:
            messages.error(request, "Уже нельзя вернуть")
        else:
            messages.success(request, "Возвращено")
        day = restored.date if restored and restored.date else datetime.date.today()
        if not request.htmx:  # type: ignore[attr-defined]
            return redirect(_day_url(day))
        if restored is None or restored.date is None:
            response = HttpResponse()
            response["HX-Reswap"] = "none"
            return with_toasts(response, request)
        context = _day_context(request.user, day)
        return sheet_saved_response(request, "core/day.html#feed", context, "#feed")


class DailyTargetUpdateView(HtmxLoginRequiredMixin, UpdateView):
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


class ServiceWorkerView(View):
    """Отдаёт service worker с корня origin, чтобы его scope покрывал всё приложение."""

    def get(self, request: HttpRequest) -> HttpResponse:
        script_path = finders.find("js/service-worker.js")
        if script_path is None:
            raise Http404("Service worker не найден")
        with open(script_path, "rb") as script:
            response = HttpResponse(script.read(), content_type="application/javascript")
        response["Service-Worker-Allowed"] = "/"
        if not settings.DEBUG:
            response["Cache-Control"] = "no-cache"
        return response


class ProductListView(HtmxLoginRequiredMixin, ListView):
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


class ProductCreateView(HtmxLoginRequiredMixin, CreateView):
    """Создаёт продукт Каталога с автором из текущего участника."""

    form_class = ProductCreateForm
    template_name = "core/product_form.html"
    success_url = reverse_lazy("core:product_list")

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        form.instance.author = self.request.user
        messages.success(self.request, "Продукт создан")
        return super().form_valid(form)


class ProductUpdateView(HtmxLoginRequiredMixin, UpdateView):
    """Редактирует любой продукт Каталога; базовая единица остаётся прежней."""

    model = Product
    form_class = ProductEditForm
    template_name = "core/product_form.html"
    context_object_name = "product"
    success_url = reverse_lazy("core:product_list")

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        messages.success(self.request, "Продукт обновлён")
        return super().form_valid(form)


class ProductDeleteView(HtmxLoginRequiredMixin, DeleteView):
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
