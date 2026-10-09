"""Расчёт дня: Приёмы пищи, итоги и прогресс к Суточной цели."""

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import TypedDict

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser

from core.models import DailyTarget, DiaryEntry, Product
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


class Slot(TypedDict):
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


def meal_slots(meals: list[Meal]) -> list[Slot]:
    """Дополняет заполненные Приёмы пищи пустыми до всех пяти в порядке `MealType`."""
    by_type = {meal["type"]: meal for meal in meals}
    slots: list[Slot] = []
    for meal_type, meal_label in DiaryEntry.MealType.choices:
        meal = by_type.get(meal_type)
        slots.append(
            {
                "type": meal_type,
                "label": meal_label,
                "entries": meal["entries"] if meal else [],
                "calories": meal["calories"] if meal else Decimal(0),
            }
        )
    return slots


def rings(totals: Macros, progress: Progress | None) -> list[Ring]:
    """Строит кольца ккал, Б, Ж, У в этом порядке; без Суточной цели колец нет."""
    if progress is None:
        return []
    target = progress["target"]
    specs = (
        ("Ккал", "calories"),
        ("Б", "proteins"),
        ("Ж", "fats"),
        ("У", "carbs"),
    )
    result: list[Ring] = []
    for label, key in specs:
        value: Decimal = totals[key]  # type: ignore[literal-required]
        goal: Decimal = getattr(target, key)
        pct = min(100, int(value / goal * 100)) if goal else 0
        result.append(Ring(label=label, value=value, goal=goal, pct=pct, over=value > goal))
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
