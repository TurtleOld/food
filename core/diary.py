"""Расчёт дня: Приёмы пищи, итоги и прогресс к Суточной цели."""

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import TypedDict

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser

from core.models import DailyTarget, DiaryEntry


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
