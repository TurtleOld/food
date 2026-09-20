import datetime
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import ProtectedError
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

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


@login_required
def day(request: HttpRequest, date: str | None = None) -> HttpResponse:
    """Render the signed-in member's personal day page with meal totals."""
    current_date = _parse_date(date) if date is not None else datetime.date.today()

    entries = DiaryEntry.objects.filter(
        member_id=request.user.pk, date=current_date
    ).select_related("product")
    meals = []
    for meal_type, meal_label in DiaryEntry.MealType.choices:
        meal_entries = [entry for entry in entries if entry.meal_type == meal_type]
        if not meal_entries:
            continue
        meals.append(
            {
                "type": meal_type,
                "label": meal_label,
                "entries": meal_entries,
                "calories": sum((entry.calories for entry in meal_entries), Decimal(0)),
                "proteins": sum((entry.proteins for entry in meal_entries), Decimal(0)),
                "fats": sum((entry.fats for entry in meal_entries), Decimal(0)),
                "carbs": sum((entry.carbs for entry in meal_entries), Decimal(0)),
            }
        )

    totals = {
        "calories": sum((entry.calories for entry in entries), Decimal(0)),
        "proteins": sum((entry.proteins for entry in entries), Decimal(0)),
        "fats": sum((entry.fats for entry in entries), Decimal(0)),
        "carbs": sum((entry.carbs for entry in entries), Decimal(0)),
    }

    target = DailyTarget.objects.filter(member_id=request.user.pk).first()
    progress = None
    if target is not None:
        progress = {
            "target": target,
            "calories": totals["calories"] - target.calories,
            "proteins": totals["proteins"] - target.proteins,
            "fats": totals["fats"] - target.fats,
            "carbs": totals["carbs"] - target.carbs,
        }

    context = {
        "current_date": current_date,
        "previous_date": current_date - datetime.timedelta(days=1),
        "next_date": current_date + datetime.timedelta(days=1),
        "meals": meals,
        "totals": totals,
        "progress": progress,
    }
    return render(request, "core/day.html", context)


@login_required
def entry_create(request: HttpRequest, date: str) -> HttpResponse:
    """Create a diary entry for the signed-in member on the given day."""
    entry_date = _parse_date(date)

    if request.method == "POST":
        form = DiaryEntryForm(request.POST)
        if form.is_valid():
            entry = form.save(commit=False)
            entry.member = request.user
            entry.date = entry_date
            entry.save()
            messages.success(request, "Запись добавлена")
            return redirect("core:day_on", date=entry_date.isoformat())
    else:
        form = DiaryEntryForm()

    context = {"form": form, "date": entry_date, "cancel_url": _day_url(entry_date)}
    return render(request, "core/entry_form.html", context)


@login_required
def entry_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Edit a diary entry belonging to the signed-in member."""
    entry = get_object_or_404(DiaryEntry, pk=pk, member_id=request.user.pk)
    if request.method == "POST":
        form = DiaryEntryEditForm(request.POST, instance=entry)
        if form.is_valid():
            form.save()
            messages.success(request, "Запись обновлена")
            return redirect("core:day_on", date=form.instance.date.isoformat())
    else:
        form = DiaryEntryEditForm(instance=entry)

    context = {"form": form, "entry": entry, "cancel_url": _day_url(entry.date)}
    return render(request, "core/entry_form.html", context)


@login_required
def entry_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete a diary entry belonging to the signed-in member."""
    entry = get_object_or_404(DiaryEntry, pk=pk, member_id=request.user.pk)
    if request.method == "POST":
        entry_date = entry.date
        entry.delete()
        messages.success(request, "Запись удалена")
        return redirect("core:day_on", date=entry_date.isoformat())
    context = {"entry": entry, "cancel_url": _day_url(entry.date)}
    return render(request, "core/entry_confirm_delete.html", context)


@login_required
def daily_target_edit(request: HttpRequest) -> HttpResponse:
    """Edit the signed-in member's own daily КБЖУ target."""
    target = DailyTarget.objects.filter(member_id=request.user.pk).first()
    if request.method == "POST":
        form = DailyTargetForm(request.POST, instance=target)
        if form.is_valid():
            daily_target = form.save(commit=False)
            daily_target.member = request.user
            daily_target.save()
            messages.success(request, "Цель сохранена")
            return redirect("core:day")
    else:
        form = DailyTargetForm(instance=target)

    context = {"form": form, "cancel_url": _day_url(datetime.date.today())}
    return render(request, "core/daily_target_form.html", context)


def healthz(request: HttpRequest) -> HttpResponse:
    """Report that the application process is up."""
    return HttpResponse("ok")


@login_required
def product_list(request: HttpRequest) -> HttpResponse:
    """Render the shared product catalog, optionally filtered by name."""
    query = request.GET.get("q", "").strip()
    all_products = Product.objects.select_related("author")
    products: list[Product] = list(all_products)
    if query:
        needle = query.lower()
        products = [product for product in products if needle in product.name.lower()]
    return render(request, "core/product_list.html", {"products": products, "query": query})


@login_required
def product_create(request: HttpRequest) -> HttpResponse:
    """Create a new catalog product authored by the signed-in member."""
    if request.method == "POST":
        form = ProductCreateForm(request.POST)
        if form.is_valid():
            product = form.save(commit=False)
            product.author = request.user
            product.save()
            messages.success(request, "Продукт создан")
            return redirect("core:product_list")
    else:
        form = ProductCreateForm()
    return render(request, "core/product_form.html", {"form": form})


@login_required
def product_edit(request: HttpRequest, pk: int) -> HttpResponse:
    """Edit any catalog product; the base unit stays fixed."""
    product = get_object_or_404(Product, pk=pk)
    if request.method == "POST":
        form = ProductEditForm(request.POST, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, "Продукт обновлён")
            return redirect("core:product_list")
    else:
        form = ProductEditForm(instance=product)
    return render(request, "core/product_form.html", {"form": form, "product": product})


@login_required
def product_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Delete a catalog product, unless a diary entry still references it."""
    product = get_object_or_404(Product, pk=pk)
    if request.method == "POST":
        try:
            product.delete()
        except ProtectedError:
            messages.error(
                request,
                "Продукт нельзя удалить: на него ссылаются записи дневника",
            )
        else:
            messages.success(request, "Продукт удалён")
        return redirect("core:product_list")
    return render(request, "core/product_confirm_delete.html", {"product": product})
