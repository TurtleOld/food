from __future__ import annotations

import copy
import datetime
import json
import secrets
from decimal import Decimal
from typing import Any, cast
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.db.models import ProtectedError, QuerySet
from django.forms import Form, ModelForm
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import Resolver404, resolve, reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    FormView,
    ListView,
    TemplateView,
    UpdateView,
)
from django_htmx.http import HttpResponseClientRedirect, trigger_client_event

from core.barcodes import normalize_barcode
from core.catalog import (
    bind_barcode,
    catalog_products,
    entries_count,
    search_by_code,
    search_catalog,
    unbind_barcode,
)
from core.catalog import deletion_token as product_deletion_token
from core.catalog import unbind_token as barcode_unbind_token
from core.diary import (
    NothingToCopy,
    addition_token,
    copy_diary,
    copy_token,
    day_summary,
    deletion_token,
    draft_entry,
    edit_token,
    entry_added_text,
    initial_meal,
    kcal_from_macros,
    last_amount,
    meal_rows,
    parse_hour,
    parse_meal,
    recent_products,
    rings,
    search_products,
)
from core.forms import (
    DailyTargetForm,
    DiaryCopyForm,
    DiaryEntryEditForm,
    DiaryEntryForm,
    DiaryEntrySheetForm,
    ProductCreateForm,
    ProductEditForm,
    ProfileForm,
    TargetAdjustmentForm,
    WeightMeasurementForm,
)
from core.htmx import (
    FragmentMixin,
    HtmxLoginRequiredMixin,
    sheet_saved_response,
    with_toasts,
)
from core.models import (
    Barcode,
    DailyTarget,
    DiaryCopyOperation,
    DiaryEntry,
    Product,
    Profile,
    WeightMeasurement,
)
from core.off import OffLookup, Outcome, lookup
from core.profile import MIN_ADULT_AGE, bmi_value, latest_weight, target_suggestion
from core.pwa import cache_name, precache_urls
from core.undo import add_undo_message, restore

QUICK_AMOUNTS = (50, 100, 150, 200)
DEFAULT_AMOUNT = 100


def _parse_date(value: str) -> datetime.date:
    """Разбирает дату из сегмента URL; неверная дата даёт Http404."""
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise Http404("Неверная дата") from None


def _pk(value: Any) -> int:
    """Разбирает pk из запроса; мусор даёт 0, то есть «не найдено»."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _day_url(date: datetime.date) -> str:
    """Собирает URL страницы дня для даты `date`."""
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
        "meal_rows": meal_rows(summary.meals),
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


_COPY_TITLES: dict[str, tuple[str, str]] = {
    DiaryCopyForm.Scope.DAY.value: (
        "Копировать день",
        "Все записи исходного дня добавятся в целевой.",
    ),
    DiaryCopyForm.Scope.MEAL.value: ("Повторить приём", "Записи приёма добавятся в целевой день."),
    DiaryCopyForm.Scope.ENTRY.value: (
        "Скопировать запись",
        "Запись добавится в выбранный день и приём пищи.",
    ),
}


class DiaryCopyView(HtmxLoginRequiredMixin, View):
    """Копирует день, Приём пищи или одну Запись в любой день текущего участника."""

    http_method_names = ["get", "post"]

    def setup(self, request: HttpRequest, *args: Any, **kwargs: Any) -> None:
        super().setup(request, *args, **kwargs)
        self.page_date = _parse_date(kwargs["date"])

    def _scope(self, data: Any, *, strict: bool) -> tuple[str, str, DiaryEntry | None]:
        """Разбирает вариант копирования из запроса; чужую Запись при `strict` не показывает."""
        scope = data.get("scope")
        entry_value = data.get("entry", "")
        meal_value = data.get("meal") or data.get("meal_type") or ""
        if scope == DiaryCopyForm.Scope.ENTRY or (scope is None and entry_value):
            entry = (
                DiaryEntry.objects.filter(member_id=self.request.user.pk, pk=_pk(entry_value))
                .select_related("product")
                .first()
            )
            if entry is None and strict:
                raise Http404("Запись не найдена")
            return DiaryCopyForm.Scope.ENTRY, "", entry
        if scope == DiaryCopyForm.Scope.MEAL or (scope is None and meal_value):
            meal = parse_meal(meal_value)
            if not meal and strict:
                raise Http404("Неверный приём пищи")
            return DiaryCopyForm.Scope.MEAL, meal, None
        return DiaryCopyForm.Scope.DAY, "", None

    def _context(
        self, form: DiaryCopyForm, scope: str, entry: DiaryEntry | None, meal: str
    ) -> dict[str, Any]:
        title, subtitle = _COPY_TITLES.get(scope, _COPY_TITLES[DiaryCopyForm.Scope.DAY.value])
        if scope == DiaryCopyForm.Scope.MEAL and meal:
            subtitle = f"Приём «{DiaryEntry.MealType(meal).label}» добавится в целевой день."
        return {"form": form, "scope": scope, "title": title, "subtitle": subtitle, "entry": entry}

    def _render(
        self, form: DiaryCopyForm, scope: str, entry: DiaryEntry | None, meal: str
    ) -> HttpResponse:
        template = (
            "core/copy_sheet.html#copy"
            if self.request.htmx  # type: ignore[attr-defined]
            else "core/copy_sheet.html"
        )
        return render(self.request, template, self._context(form, scope, entry, meal))

    def _redirect(self, target: datetime.date) -> HttpResponse:
        url = _day_url(target)
        if self.request.htmx:  # type: ignore[attr-defined]
            return HttpResponseClientRedirect(url)
        return redirect(url)

    def get(self, request: HttpRequest, date: str) -> HttpResponse:
        scope, meal, entry = self._scope(request.GET, strict=True)
        form = DiaryCopyForm(
            initial={
                "scope": scope,
                "source_date": entry.date if entry else self.page_date,
                "target_date": timezone.localdate(),
                "meal_type": meal,
                "entry": entry.pk if entry else "",
                "target_meal_type": entry.meal_type if entry else "",
                "operation": secrets.token_urlsafe(16),
            },
            member=request.user,
        )
        return self._render(form, scope, entry, meal)

    def post(self, request: HttpRequest, date: str) -> HttpResponse:
        form = DiaryCopyForm(request.POST, member=request.user)
        scope, meal, entry = self._scope(request.POST, strict=False)
        if not form.is_valid():
            return self._render(form, scope, entry, meal)
        cleaned = form.cleaned_data
        target: datetime.date = cleaned["target_date"]
        operation: str = cleaned["operation"]
        entries: list[DiaryEntry] = []
        duplicate = False
        try:
            with transaction.atomic():
                _, created = DiaryCopyOperation.objects.get_or_create(
                    member_id=request.user.pk, key=operation
                )
                if created:
                    entries = copy_diary(
                        request.user,
                        source_date=cleaned["source_date"],
                        target_date=target,
                        meal_type=cleaned.get("meal_type") or None,
                        entry=cleaned.get("entry"),
                        target_meal_type=cleaned.get("target_meal_type") or None,
                    )
                else:
                    duplicate = True
        except NothingToCopy:
            empty_message = {
                DiaryCopyForm.Scope.MEAL.value: "В выбранном приёме нет записей",
                DiaryCopyForm.Scope.ENTRY.value: "Запись не найдена",
            }.get(scope, "В исходном дне нет записей")
            form.add_error(None, empty_message)
            return self._render(form, scope, entry, meal)
        if duplicate:
            messages.info(request, "Это копирование уже выполнено")
            return self._redirect(target)
        add_undo_message(
            request, f"Скопировано записей: {len(entries)}", copy_token(entries, target)
        )
        return self._redirect(target)


class OwnEntryMixin(HtmxLoginRequiredMixin):
    """Ограничивает записи дневника записями текущего участника."""

    request: HttpRequest
    model = DiaryEntry

    def get_queryset(self) -> QuerySet[DiaryEntry]:
        return DiaryEntry.objects.filter(member_id=self.request.user.pk)


class SheetModeMixin:
    """Различает запрос из шторки (htmx) и обычную страницу."""

    request: HttpRequest

    @property
    def in_sheet(self) -> bool:
        return bool(self.request.htmx)  # type: ignore[attr-defined]


class BarcodeAddMixin:
    """Кнопка «добавить» у Цифр штрихкода: привязывает только код, остальные правки не сохраняет.

    У существующего Продукта код привязывается сразу; у нового запоминается чипом до создания.
    Шторка остаётся открытой, введённые значения формы сохраняются.
    """

    request: HttpRequest
    kwargs: dict[str, Any]
    object: Any

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if "add_barcode" not in request.POST:
            return super().post(request, *args, **kwargs)  # type: ignore[misc]
        self.object = self.get_object() if "pk" in self.kwargs else None  # type: ignore[attr-defined]
        form = self.get_form()  # type: ignore[attr-defined]
        form.full_clean()
        code = None if "barcode" in form.errors else form.cleaned_data.get("barcode")
        if code and self.object:
            bind_barcode(code, self.object)
        context = self.get_context_data(form=form.draft_for_barcode_step())  # type: ignore[attr-defined]
        if code and not self.object:
            context["bound_code"] = code
        return self.render_to_response(context)  # type: ignore[attr-defined]


class EntryCreateView(HtmxLoginRequiredMixin, SheetModeMixin, CreateView):
    """Создаёт запись дневника текущего участника на заданный день.

    Без htmx это обычная страница с формой. Из шторки GET без `product` — шаг поиска,
    с `product` — шаг количества, а POST отвечает лентой дня и тостом с «Вернуть».
    """

    template_name = "core/entry_form.html"

    def setup(self, request: HttpRequest, *args: Any, **kwargs: Any) -> None:
        super().setup(request, *args, **kwargs)
        self.entry_date = _parse_date(kwargs["date"])

    def get_form_class(self) -> type[ModelForm[Any]]:
        return DiaryEntrySheetForm if self.in_sheet else DiaryEntryForm

    def get_template_names(self) -> list[str]:
        if not self.in_sheet:
            return super().get_template_names()
        if self.request.method == "GET" and "product" not in self.request.GET:
            return ["core/entry_sheet.html#search"]
        return ["core/entry_sheet.html#amount"]

    def _meal(self) -> str:
        carried = _carried_meal(self.request.GET)
        return initial_meal(
            carried.get("meal", ""), carried.get("hour", ""), datetime.datetime.now()
        )

    def get_initial(self) -> dict[str, Any]:
        initial: dict[str, Any] = {"date": self.entry_date, "meal_type": self._meal()}
        if "product" in self.request.GET:
            product = get_object_or_404(Product, pk=_pk(self.request.GET["product"]))
            initial["product"] = product.pk
            initial["amount"] = last_amount(self.request.user, product) or DEFAULT_AMOUNT
        return initial

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        form.instance.member = self.request.user
        if not self.in_sheet:
            form.instance.date = self.entry_date
            messages.success(self.request, "Запись добавлена")
            return super().form_valid(form)
        super().form_valid(form)
        entry: DiaryEntry = form.instance
        add_undo_message(self.request, entry_added_text(entry), addition_token(entry))
        context = _day_context(self.request.user, self.entry_date)
        return sheet_saved_response(self.request, "core/day.html#feed", context, "#feed")

    def get_success_url(self) -> str:
        return _day_url(self.entry_date)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.update(date=self.entry_date, cancel_url=_day_url(self.entry_date))
        if self.in_sheet:
            carried = _carried_meal(self.request.GET)
            context["bind"] = normalize_barcode(self.request.GET.get("bind", "")) or ""
            context["code"] = normalize_barcode(self.request.GET.get("code", "")) or ""
            context.update(meal=carried.get("meal", ""), hour=carried.get("hour", ""))
            form = context["form"]
            product = Product.objects.filter(pk=_pk(form["product"].value())).first()
            context.update(
                meal_label=DiaryEntry.MealType(form["meal_type"].value() or self._meal()).label,
                product=product,
                last_amount=last_amount(self.request.user, product) if product else None,
                quick_amounts=QUICK_AMOUNTS,
                recent=recent_products(self.request.user),
            )
            if product:
                context["macros"] = draft_entry(product, form["amount"].value())
        return context


class EntrySearchView(HtmxLoginRequiredMixin, TemplateView):
    """Фрагмент результатов шага поиска: «Недавние» при пустом запросе, иначе совпадения."""

    template_name = "core/entry_sheet.html#results"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get("q", "").strip()
        context.update(
            date=_parse_date(self.kwargs["date"]),
            **{"meal": "", "hour": "", **_carried_meal(self.request.GET)},
            query=query,
            bind=normalize_barcode(self.request.GET.get("bind", "")) or "",
        )
        code_search = None if context["bind"] else search_by_code(query)
        if code_search:
            context["code_search"] = code_search
            context["products"] = code_search.products
        elif query:
            context["products"] = (
                search_catalog(query) if context["bind"] else search_products(query)
            )
        else:
            context["recent"] = recent_products(self.request.user)
        return context


class EntryProductCreateView(HtmxLoginRequiredMixin, BarcodeAddMixin, CreateView):
    """Шаг 1б шторки: новый Продукт по пути, после создания — шаг количества."""

    form_class = ProductCreateForm
    template_name = "core/product_sheet.html"

    def setup(self, request: HttpRequest, *args: Any, **kwargs: Any) -> None:
        super().setup(request, *args, **kwargs)
        self.entry_date = _parse_date(kwargs["date"])

    def _code(self) -> str:
        return normalize_barcode(self.request.GET.get("code", "")) or ""

    def _off(self) -> OffLookup | None:
        """Ответ Open Food Facts для кода, которого нет в Каталоге; иначе `None`."""
        code = self._code()
        if not code or Barcode.objects.filter(code=code).exists():
            return None
        return lookup(code)

    def get_initial(self) -> dict[str, Any]:
        initial: dict[str, Any] = {
            "name": self.request.GET.get("name", "").strip(),
            "barcode": self._code(),
        }
        off = self._off()
        if off and off.outcome is Outcome.FOUND:
            initial.update(
                name=initial["name"] or off.name,
                base_unit=off.base_unit,
                calories=off.calories,
                proteins=off.proteins,
                fats=off.fats,
                carbs=off.carbs,
            )
        return initial

    def _carried(self) -> dict[str, str]:
        carried = _carried_meal(self.request.GET)
        if code := self._code():
            carried["code"] = code
        return carried

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        form.instance.author = self.request.user
        return super().form_valid(form)

    def get_success_url(self) -> str:
        product: Product = self.object
        carried = {key: value for key, value in self._carried().items() if key != "code"}
        query = urlencode({"product": product.pk, **carried})
        return f"{reverse('core:entry_create', args=[self.entry_date.isoformat()])}?{query}"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.update(
            date=self.entry_date,
            carried=urlencode(self._carried()),
            bound_code=self._code(),
            off=self._off(),
            **{"meal": "", "hour": "", **_carried_meal(self.request.GET)},
        )
        return context


def _carried_meal(data: Any) -> dict[str, str]:
    """Приём пищи и час, которые шторка переносит между шагами; невалидные отбрасываются."""
    carried = {"meal": parse_meal(data.get("meal", "")), "hour": parse_hour(data.get("hour", ""))}
    return {key: value for key, value in carried.items() if value}


class EntryBarcodeActionView(HtmxLoginRequiredMixin, View):
    """Каркас действий над кодом в шторке: разобрать код, выполнить, перейти к следующему шагу."""

    http_method_names = ["post"]
    next_route: str

    def apply(self, request: HttpRequest, code: str) -> dict[str, str]:
        """Выполняет действие и возвращает параметры первого места в запросе следующего шага."""
        raise NotImplementedError

    def post(self, request: HttpRequest, date: str) -> HttpResponse:
        code = normalize_barcode(request.POST.get("code", ""))
        if code is None:
            raise Http404("Неверный штрихкод")
        query = {**self.apply(request, code), **_carried_meal(request.POST)}
        url = reverse(self.next_route, args=[_parse_date(date).isoformat()])
        return redirect(f"{url}?{urlencode(query)}")


class EntryBarcodeBindView(EntryBarcodeActionView):
    """Привязывает код к выбранному в шторке Продукту и ведёт на шаг количества."""

    next_route = "core:entry_create"

    def apply(self, request: HttpRequest, code: str) -> dict[str, str]:
        product = get_object_or_404(Product, pk=_pk(request.POST.get("product")))
        return {"product": str(bind_barcode(code, product).product_id)}


class EntryBarcodeUnbindView(EntryBarcodeActionView):
    """«Не тот продукт?»: отвязывает код и возвращает к форме нового Продукта с этим кодом."""

    next_route = "core:entry_product_new"

    def apply(self, request: HttpRequest, code: str) -> dict[str, str]:
        unbind_barcode(code)
        return {"code": code}


class EntryDraftPreviewView(HtmxLoginRequiredMixin, TemplateView):
    """Живой пересчёт КБЖУ ещё не созданной записи по текущему Каталогу."""

    template_name = "core/entry_sheet.html#macros"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        product = get_object_or_404(Product, pk=_pk(self.request.GET.get("product")))
        context["macros"] = draft_entry(product, self.request.GET.get("amount"))
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
        if not self.request.htmx:  # type: ignore[attr-defined]
            messages.success(self.request, "Запись обновлена")
            return super().form_valid(form)
        add_undo_message(self.request, "Запись обновлена", self.undo_token)
        super().form_valid(form)
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


def _shown_day(request: HttpRequest) -> datetime.date | None:
    """День, открытый на странице по `HX-Current-URL`; `None`, если страница не день."""
    path = request.htmx.current_url_abs_path  # type: ignore[attr-defined]
    if not path:
        return None
    try:
        match = resolve(path.split("?")[0])
    except Resolver404:
        return None
    if match.url_name == "day":
        return datetime.date.today()
    if match.url_name == "day_on":
        try:
            return datetime.date.fromisoformat(match.kwargs["date"])
        except ValueError:
            return None
    return None


class UndoView(HtmxLoginRequiredMixin, View):
    """Применяет подписанный снимок из тоста «Вернуть»."""

    def post(self, request: HttpRequest) -> HttpResponse:
        restored = restore(request.user, request.POST.get("token", ""))
        if restored is None:
            messages.error(request, "Уже нельзя вернуть")
        else:
            messages.success(request, "Возвращено")
        if not request.htmx:  # type: ignore[attr-defined]
            day = restored.date if restored and restored.date else datetime.date.today()
            return redirect(_day_url(day))
        if restored is not None and restored.date is not None:
            # Лента — день страницы, а не записи: после переноса даты они различаются.
            day = _shown_day(request) or restored.date
            context = _day_context(request.user, day)
            return sheet_saved_response(request, "core/day.html#feed", context, "#feed")
        if restored is not None and restored.product_id is not None:
            product = Product.objects.filter(pk=restored.product_id).first()
            html = render_to_string(
                "components/barcode_chips.html", {"product": product, "oob": True}, request=request
            )
            response = with_toasts(HttpResponse(html), request)
        else:
            response = with_toasts(HttpResponse(), request)
        response["HX-Reswap"] = "none"
        if restored is not None:
            trigger_client_event(response, "catalog:changed")
        return response


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
        target = self.object
        context["has_target"] = target is not None
        if target is not None:
            context["macro_kcal"] = kcal_from_macros(target.proteins, target.fats, target.carbs)
        return context


class ProfileView(HtmxLoginRequiredMixin, FormView):
    """Профиль Члена семьи: антропометрия, Замеры веса, ИМТ и предложение цели.

    Профиль и Замер сохраняются разными формами на одной странице. Расчёт цели —
    только предложение: в Суточную цель он попадает после явной отправки формы.
    """

    form_class = ProfileForm
    template_name = "core/profile.html"
    success_url = reverse_lazy("core:profile")
    weight_form: WeightMeasurementForm | None = None

    def get_profile(self) -> Profile:
        profile = Profile.objects.filter(member_id=self.request.user.pk).first()
        return profile or Profile(member_id=cast(int, self.request.user.pk))

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_profile()
        return kwargs

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        Profile.objects.update_or_create(
            member_id=cast(int, self.request.user.pk),
            defaults={
                "height_cm": form.cleaned_data["height_cm"],
                "birth_date": form.cleaned_data["birth_date"],
                "sex": form.cleaned_data["sex"],
                "activity": form.cleaned_data["activity"],
            },
        )
        messages.success(self.request, "Профиль сохранён")
        return super().form_valid(form)

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if "weight_kg" in request.POST:
            return self._post_weight(request)
        return super().post(request, *args, **kwargs)

    def _post_weight(self, request: HttpRequest) -> HttpResponse:
        form = WeightMeasurementForm(request.POST)
        if form.is_valid():
            form.instance.member = self.request.user
            form.save()
            messages.success(request, "Замер веса добавлен")
            return redirect("core:profile")
        self.weight_form = form
        return self.render_to_response(
            self.get_context_data(form=ProfileForm(instance=self.get_profile()))
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        profile = self.get_profile()
        today = timezone.localdate()
        adjustment_form = TargetAdjustmentForm(self.request.GET or None)
        adjustment = Decimal(0)
        if adjustment_form.is_valid():
            adjustment = adjustment_form.cleaned_data["adjustment"]
        weight = latest_weight(self.request.user)
        base_suggestion = target_suggestion(self.request.user)
        suggestion = (
            target_suggestion(self.request.user, adjustment) if adjustment else base_suggestion
        )
        context.update(
            profile=profile,
            is_adult=(profile.age_on(today) or 0) >= MIN_ADULT_AGE,
            weight_form=self.weight_form or WeightMeasurementForm(initial={"date": today}),
            measurements=WeightMeasurement.objects.filter(member_id=self.request.user.pk),
            bmi=bmi_value(profile.height_cm, weight.weight_kg if weight else None),
            adjustment_form=adjustment_form,
            suggestion=suggestion,
            adjustment_too_large=bool(adjustment and base_suggestion and suggestion is None),
            target=DailyTarget.objects.filter(member_id=self.request.user.pk).first(),
        )
        return context


class HealthzView(View):
    """Сообщает, что процесс приложения жив."""

    def get(self, request: HttpRequest) -> HttpResponse:
        return HttpResponse("ok")


class ServiceWorkerView(View):
    """Отдаёт service worker с корня origin, чтобы его scope покрывал всё приложение.

    Скрипт рендерится шаблоном: список precache состоит из хэшированных URL
    статики, а имя кэша выводится из него, поэтому новая статика даёт новые
    байты воркера и браузер ставит свежую версию.
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        urls = precache_urls()
        response = render(
            request,
            "service-worker.js",
            {
                "precache_json": json.dumps(urls),
                "cache_name": cache_name(urls),
                "offline_url": static("offline.html"),
                "static_prefix": static(""),
                "bypass_cache": settings.DEBUG,
            },
            content_type="application/javascript",
        )
        response["Service-Worker-Allowed"] = "/"
        response["Cache-Control"] = "no-cache"
        return response


def _catalog_context(query: str = "") -> dict[str, Any]:
    """Контекст фрагмента списка Каталога: совпадения с запросом и общее число продуктов."""
    return {
        "products": catalog_products(query),
        "total": Product.objects.count(),
        "query": query,
    }


class ProductListView(HtmxLoginRequiredMixin, FragmentMixin, ListView):
    """Общий Каталог продуктов с фильтром по названию."""

    template_name = "core/product_list.html"
    fragment_template_name = "core/product_list.html#catalog"
    context_object_name = "products"

    def get_queryset(self) -> QuerySet[Product]:
        return catalog_products(self.query())

    def query(self) -> str:
        return self.request.GET.get("q", "").strip()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.update(total=Product.objects.count(), query=self.query())
        return context


class CatalogSheetMixin(SheetModeMixin):
    """Продукт Каталога в шторке при htmx-запросе; без htmx остаётся обычная страница."""

    def get_template_names(self) -> list[str]:
        if self.in_sheet:
            return ["core/catalog_sheet.html"]
        return super().get_template_names()  # type: ignore[misc]

    def saved(self, text: str) -> HttpResponse:
        """Ответ шторки на успешное сохранение: свежий список, тост, закрытие."""
        messages.success(self.request, text)
        return sheet_saved_response(
            self.request, "core/product_list.html#catalog", _catalog_context(), "#catalog"
        )


class ProductCreateView(HtmxLoginRequiredMixin, BarcodeAddMixin, CatalogSheetMixin, CreateView):
    """Создаёт продукт Каталога с автором из текущего участника."""

    form_class = ProductCreateForm
    template_name = "core/product_form.html"
    success_url = reverse_lazy("core:product_list")

    def get_initial(self) -> dict[str, Any]:
        return {"name": self.request.GET.get("name", "").strip()}

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        form.instance.author = self.request.user
        if self.in_sheet:
            form.save()
            return self.saved("Продукт создан")
        messages.success(self.request, "Продукт создан")
        return super().form_valid(form)


class ProductUpdateView(HtmxLoginRequiredMixin, BarcodeAddMixin, CatalogSheetMixin, UpdateView):
    """Редактирует любой продукт Каталога; базовая единица остаётся прежней."""

    model = Product
    form_class = ProductEditForm
    template_name = "core/product_form.html"
    context_object_name = "product"
    success_url = reverse_lazy("core:product_list")

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        if self.in_sheet:
            form.save()
            return self.saved("Продукт обновлён")
        messages.success(self.request, "Продукт обновлён")
        return super().form_valid(form)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["usage"] = entries_count(self.object)
        return context


class BarcodeUnbindView(HtmxLoginRequiredMixin, DeleteView):
    """Отвязывает Штрихкод от Продукта сразу и предлагает «Вернуть»."""

    model = Barcode
    http_method_names = ["post"]

    def form_valid(self, form: Form) -> HttpResponse:
        barcode: Barcode = self.object
        product = barcode.product
        token = barcode_unbind_token(barcode, self.request.user)
        barcode.delete()
        add_undo_message(self.request, "Штрихкод отвязан", token)
        if not self.request.htmx:  # type: ignore[attr-defined]
            return redirect("core:product_edit", pk=product.pk)
        html = render_to_string(
            "components/barcode_chips.html", {"product": product}, request=self.request
        )
        response = with_toasts(HttpResponse(html), self.request)
        trigger_client_event(response, "catalog:changed")
        return response


class ProductDeleteView(HtmxLoginRequiredMixin, DeleteView):
    """Удаляет продукт Каталога, если на него не ссылаются записи дневника.

    Из шторки (htmx) удаляет сразу и предлагает «Вернуть»; без JS — через страницу подтверждения.
    """

    model = Product
    template_name = "core/product_confirm_delete.html"
    context_object_name = "product"
    success_url = reverse_lazy("core:product_list")

    def form_valid(self, form: Form) -> HttpResponse:
        if self.request.htmx:  # type: ignore[attr-defined]
            return self._delete_from_sheet()
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

    def _delete_from_sheet(self) -> HttpResponse:
        product: Product = self.object
        if entries_count(product):
            messages.error(self.request, "Удалить нельзя — продукт есть в записях дневника")
            response = HttpResponse()
            response["HX-Reswap"] = "none"
            return with_toasts(response, self.request)
        token = product_deletion_token(product, self.request.user)
        product.delete()
        add_undo_message(self.request, "Продукт удалён", token)
        return sheet_saved_response(
            self.request, "core/product_list.html#catalog", _catalog_context(), "#catalog"
        )
