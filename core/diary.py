"""Расчёт дня: Приёмы пищи, итоги и прогресс к Суточной цели."""

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import TypedDict

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser

from core.forms import DiaryEntrySheetForm
from core.models import DailyTarget, DiaryEntry, Product
from core.nutrition import NUTRITION_ROWS
from core.undo import CannotUndo, Member, Restored, make_token, restorer


class Macros(TypedDict):
    """КБЖУ: калории, белки, жиры, углеводы."""

    calories: Decimal
    proteins: Decimal
    fats: Decimal
    carbs: Decimal


class Meal(Macros):
    """Приём пищи дня с его записями и итогами."""

    type: str
    label: str
    entries: list[DiaryEntry]


class Progress(Macros):
    """Отклонение итога дня от Суточной цели (итог минус цель)."""

    target: DailyTarget


@dataclass(frozen=True)
class DaySummary:
    """Приёмы пищи дня, суточный итог и прогресс к цели (None, если цели нет)."""

    meals: list[Meal]
    totals: Macros
    progress: Progress | None


def _sum_macros(entries: list[DiaryEntry]) -> Macros:
    return {
        "calories": sum((entry.calories for entry in entries), Decimal(0)),
        "proteins": sum((entry.proteins for entry in entries), Decimal(0)),
        "fats": sum((entry.fats for entry in entries), Decimal(0)),
        "carbs": sum((entry.carbs for entry in entries), Decimal(0)),
    }


def day_summary(member: AbstractBaseUser | AnonymousUser, date: datetime.date) -> DaySummary:
    """Собирает сводку дня участника из снапшотов записей дневника.

    Args:
        member: участник, чей дневник читается.
        date: день сводки.

    Returns:
        Непустые Приёмы пищи в порядке `MealType`, суточный итог и прогресс
        к Суточной цели (None, если цель не задана).
    """
    entries = list(
        DiaryEntry.objects.filter(member_id=member.pk, date=date).select_related("product")
    )

    meals: list[Meal] = []
    for meal_type, meal_label in DiaryEntry.MealType.choices:
        meal_entries = [entry for entry in entries if entry.meal_type == meal_type]
        if not meal_entries:
            continue
        macros = _sum_macros(meal_entries)
        meals.append(
            {
                "type": meal_type,
                "label": meal_label,
                "entries": meal_entries,
                **macros,
            }
        )

    totals = _sum_macros(entries)
    target = DailyTarget.objects.filter(member_id=member.pk).first()
    progress: Progress | None = None
    if target is not None:
        progress = {
            "target": target,
            "calories": totals["calories"] - target.calories,
            "proteins": totals["proteins"] - target.proteins,
            "fats": totals["fats"] - target.fats,
            "carbs": totals["carbs"] - target.carbs,
        }
    return DaySummary(meals=meals, totals=totals, progress=progress)


def kcal_from_macros(proteins: Decimal, fats: Decimal, carbs: Decimal) -> Decimal:
    """Считает калорийность по БЖУ: Б×4 + Ж×9 + У×4."""
    return proteins * 4 + fats * 9 + carbs * 4


class MealRow(TypedDict):
    """Приём пищи ленты дня: заполненный или пустой."""

    type: str
    label: str
    entries: list[DiaryEntry]
    calories: Decimal


@dataclass(frozen=True)
class Ring:
    """Кольцо прогресса: значение дня против цели.

    Attributes:
        pct: заполнение кольца, 0-100.
        over: значение превысило цель.
    """

    label: str
    value: Decimal
    goal: Decimal
    pct: int
    over: bool


def meal_rows(meals: list[Meal]) -> list[MealRow]:
    """Дополняет заполненные Приёмы пищи пустыми до всех пяти в порядке `MealType`."""
    by_type = {meal["type"]: meal for meal in meals}
    rows: list[MealRow] = []
    for meal_type, meal_label in DiaryEntry.MealType.choices:
        meal = by_type.get(meal_type)
        rows.append(
            {
                "type": meal_type,
                "label": meal_label,
                "entries": meal["entries"] if meal else [],
                "calories": meal["calories"] if meal else Decimal(0),
            }
        )
    return rows


def rings(totals: Macros, progress: Progress | None) -> list[Ring]:
    """Строит кольца ккал, Б, Ж, У в этом порядке; без Суточной цели колец нет."""
    if progress is None:
        return []
    target = progress["target"]
    result: list[Ring] = []
    for key, _dot, label in NUTRITION_ROWS:
        value: Decimal = totals[key]  # type: ignore[literal-required]
        goal: Decimal = getattr(target, key)
        pct = min(100, int(value / goal * 100)) if goal else 0
        result.append(
            Ring(label=label.capitalize(), value=value, goal=goal, pct=pct, over=value > goal)
        )
    return result


def last_amount(
    member: AbstractBaseUser | AnonymousUser, product: Product, exclude: DiaryEntry | None = None
) -> Decimal | None:
    """Возвращает последнее количество продукта у участника (None, если записей нет).

    Args:
        member: участник, чей дневник читается.
        product: продукт записи.
        exclude: запись, которую не учитывать (правимая сейчас).
    """
    entries = DiaryEntry.objects.filter(member_id=member.pk, product=product)
    if exclude is not None:
        entries = entries.exclude(pk=exclude.pk)
    latest = entries.order_by("-created_at", "-pk").first()
    return latest.amount if latest else None


def deletion_token(entry: DiaryEntry) -> str:
    """Токен «Вернуть» для удаляемой записи: хранит и КБЖУ-снапшот."""
    return make_token(
        entry.member,
        "entry_deleted",
        {
            "product": entry.product_id,
            "date": entry.date.isoformat(),
            "meal_type": entry.meal_type,
            "amount": str(entry.amount),
            "calories": str(entry.calories_snapshot),
            "proteins": str(entry.proteins_snapshot),
            "fats": str(entry.fats_snapshot),
            "carbs": str(entry.carbs_snapshot),
        },
    )


def edit_token(entry: DiaryEntry) -> str:
    """Токен «Вернуть» для правки: хранит значения записи до сохранения."""
    return make_token(
        entry.member,
        "entry_edited",
        {
            "entry": entry.pk,
            "date": entry.date.isoformat(),
            "meal_type": entry.meal_type,
            "amount": str(entry.amount),
        },
    )


@restorer("entry_deleted")
def _restore_deleted_entry(member: Member, payload: dict[str, str]) -> Restored:
    if not Product.objects.filter(pk=payload["product"]).exists():
        raise CannotUndo
    entry = DiaryEntry.objects.create(
        member=member,  # type: ignore[misc]
        product_id=payload["product"],
        date=datetime.date.fromisoformat(payload["date"]),
        meal_type=payload["meal_type"],
        amount=Decimal(payload["amount"]),
    )
    # Первое сохранение берёт снапшот из Каталога; возвращаем тот, что был у удалённой записи.
    entry.calories_snapshot = Decimal(payload["calories"])
    entry.proteins_snapshot = Decimal(payload["proteins"])
    entry.fats_snapshot = Decimal(payload["fats"])
    entry.carbs_snapshot = Decimal(payload["carbs"])
    entry.save()
    return Restored(entry.date)


@restorer("entry_edited")
def _restore_edited_entry(member: Member, payload: dict[str, str]) -> Restored:
    updated = DiaryEntry.objects.filter(pk=payload["entry"], member_id=member.pk).update(
        date=datetime.date.fromisoformat(payload["date"]),
        meal_type=payload["meal_type"],
        amount=Decimal(payload["amount"]),
    )
    if not updated:
        raise CannotUndo
    return Restored(datetime.date.fromisoformat(payload["date"]))


def parse_meal(value: str) -> str:
    """Приём пищи из параметра запроса; пустая строка, если значение не из `MealType`."""
    return value if value in DiaryEntry.MealType.values else ""


def parse_hour(value: str) -> str:
    """Час из параметра запроса как строка; пустая строка, если это не целое от 0 до 23."""
    try:
        hour = int(value)
    except ValueError:
        return ""
    return str(hour) if 0 <= hour <= 23 else ""


def initial_meal(meal: str, hour: str, now: datetime.datetime) -> str:
    """Приём пищи по умолчанию: выбранный, иначе по часу клиента, иначе по часу сервера.

    Args:
        meal: уже проверенный `parse_meal` приём пищи или пустая строка.
        hour: уже проверенный `parse_hour` час или пустая строка.
        now: текущее время сервера, если клиент не прислал час.
    """
    if meal:
        return meal
    return meal_for_hour(int(hour) if hour else now.hour)


def entry_added_text(entry: DiaryEntry) -> str:
    """Текст тоста о добавленной записи: «Продукт, 100 г → Обед»."""
    amount = f"{entry.amount:g} {entry.product.get_base_unit_display()}"
    return f"{entry.product.name}, {amount} → {entry.get_meal_type_display()}"


def draft_entry(product: Product, amount: object) -> DiaryEntry | None:
    """Несохранённая запись для показа КБЖУ; `None`, если количество не проходит валидацию."""
    form = DiaryEntrySheetForm(
        {
            "date": datetime.date.today(),
            "meal_type": DiaryEntry.MealType.LUNCH,
            "product": product.pk,
            "amount": amount or "",
        }
    )
    if not form.is_valid():
        return None
    entry = form.instance
    entry.calories_snapshot = product.calories
    entry.proteins_snapshot = product.proteins
    entry.fats_snapshot = product.fats
    entry.carbs_snapshot = product.carbs
    return entry


def meal_for_hour(hour: int) -> str:
    """Приём пищи по часу суток: до 10 завтрак, до 12 второй завтрак, до 15 обед, до 18 полдник."""
    meal_type = DiaryEntry.MealType
    if hour < 10:
        return meal_type.BREAKFAST
    if hour < 12:
        return meal_type.SECOND_BREAKFAST
    if hour < 15:
        return meal_type.LUNCH
    if hour < 18:
        return meal_type.AFTERNOON_SNACK
    return meal_type.DINNER


@dataclass(frozen=True)
class RecentProduct:
    """Недавно съеденный продукт и количество, с которым его ели в последний раз."""

    product: Product
    amount: Decimal


def recent_products(member: Member, limit: int = 8) -> list[RecentProduct]:
    """Продукты из записей участника: последний съеденный первым, каждый один раз."""
    entries = (
        DiaryEntry.objects.filter(member_id=member.pk)
        .select_related("product")
        .order_by("-created_at", "-pk")
    )
    recent: dict[int, RecentProduct] = {}
    for entry in entries.iterator():
        if entry.product_id not in recent:
            recent[entry.product_id] = RecentProduct(entry.product, entry.amount)
            if len(recent) == limit:
                break
    return list(recent.values())


def search_products(query: str) -> list[Product]:
    """Продукты Каталога, в названии которых есть `query` без учёта регистра."""
    needle = query.strip().lower()
    # SQLite lower()/icontains не сворачивают регистр кириллицы, поэтому фильтруем в Python.
    return [product for product in Product.objects.all() if needle in product.name.lower()]


def addition_token(entry: DiaryEntry) -> str:
    """Токен «Вернуть» для добавленной записи: возврат удаляет именно её."""
    return make_token(
        entry.member, "entry_added", {"entry": entry.pk, "date": entry.date.isoformat()}
    )


@restorer("entry_added")
def _restore_added_entry(member: Member, payload: dict[str, str]) -> Restored:
    deleted, _ = DiaryEntry.objects.filter(pk=payload["entry"], member_id=member.pk).delete()
    if not deleted:
        raise CannotUndo
    return Restored(datetime.date.fromisoformat(payload["date"]))
