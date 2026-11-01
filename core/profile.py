"""Профиль, Замеры веса и предложение Суточной цели.

Коэффициенты активности взяты из классификации PAL (physical activity level)
FAO/WHO/UNU, «Human Energy Requirements» (2004), таблица 5.2: сидячий образ
жизни 1.40–1.69, умеренная активность 1.70–1.99, высокая 2.00–2.40. В расчёте
использованы нижние границы диапазонов — консервативная оценка.

Миффлин — Сан Жеор: Mifflin M. D. et al. A new predictive equation for resting
energy expenditure in healthy individuals. Am J Clin Nutr. 1990;51(2):241–247.
DOI: 10.1093/ajcn/51.2.241.
"""

import datetime
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser

from core.models import Profile, WeightMeasurement

MIN_ADULT_AGE = 18
MIN_ADJUSTMENT_KCAL = Decimal("-1000")
MAX_ADJUSTMENT_KCAL = Decimal("1000")

ACTIVITY_FACTORS: dict[str, Decimal] = {
    Profile.Activity.SEDENTARY: Decimal("1.4"),
    Profile.Activity.MODERATE: Decimal("1.7"),
    Profile.Activity.HIGH: Decimal("2.0"),
}

PROTEIN_SHARE = Decimal("0.20")
FAT_SHARE = Decimal("0.30")
CARB_SHARE = Decimal("0.50")

_KCAL_PER_GRAM = {"proteins": Decimal(4), "fats": Decimal(9), "carbs": Decimal(4)}


@dataclass(frozen=True)
class TargetSuggestion:
    """Предложение Суточной цели: поддержание, поправка и раскладка БЖУ."""

    maintenance_kcal: Decimal
    adjustment_kcal: Decimal
    calories: Decimal
    proteins: Decimal
    fats: Decimal
    carbs: Decimal


def profile_of(member: AbstractBaseUser | AnonymousUser) -> Profile | None:
    """Профиль Члена семьи; None, пока Профиль ни разу не сохраняли."""
    return Profile.objects.filter(member_id=member.pk).first()


def latest_weight(member: AbstractBaseUser | AnonymousUser) -> WeightMeasurement | None:
    """Последний по дате Замер веса Члена семьи."""
    return WeightMeasurement.objects.filter(member_id=member.pk).first()


def bmi_value(height_cm: int | None, weight_kg: Decimal | None) -> Decimal | None:
    """ИМТ по росту и весу; None, если одного из значений нет."""
    if not height_cm or weight_kg is None:
        return None
    height_m = Decimal(height_cm) / 100
    return (weight_kg / (height_m * height_m)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _maintenance_kcal(
    profile: Profile, weight_kg: Decimal, on_date: datetime.date
) -> Decimal | None:
    if (
        profile.height_cm is None
        or profile.birth_date is None
        or not profile.sex
        or not profile.activity
    ):
        return None
    age = profile.age_on(on_date)
    if age is None or age < MIN_ADULT_AGE:
        return None
    factor = ACTIVITY_FACTORS.get(profile.activity)
    if factor is None:
        return None
    sex_constant = Decimal(5) if profile.sex == Profile.Sex.MALE else Decimal(-161)
    resting = (
        Decimal(10) * weight_kg
        + Decimal("6.25") * profile.height_cm
        - Decimal(5) * age
        + sex_constant
    )
    return (resting * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _grams(calories: Decimal, share: Decimal, per_gram: Decimal) -> Decimal:
    return (calories * share / per_gram).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def target_suggestion(
    member: AbstractBaseUser | AnonymousUser,
    adjustment_kcal: Decimal = Decimal(0),
    on_date: datetime.date | None = None,
) -> TargetSuggestion | None:
    """Предложение цели по Миффлину — Сан Жеору и ручной поправке.

    Args:
        member: Член семьи, чьи Профиль и последний Замер используются.
        adjustment_kcal: Поправка к поддержанию; отрицательная — снижение.
        on_date: Дата, на которую считается возраст; по умолчанию сегодня.

    Returns:
        Поддержание, поправка, калории и БЖУ с раскладкой 20/30/50% либо None,
        если Профиль неполный, Член семьи младше 18 лет или данных не хватает.
    """
    profile = profile_of(member)
    weight = latest_weight(member)
    if profile is None or weight is None:
        return None
    maintenance = _maintenance_kcal(profile, weight.weight_kg, on_date or datetime.date.today())
    if maintenance is None:
        return None
    calories = maintenance + adjustment_kcal
    if calories <= 0:
        return None
    return TargetSuggestion(
        maintenance_kcal=maintenance,
        adjustment_kcal=adjustment_kcal,
        calories=calories,
        proteins=_grams(calories, PROTEIN_SHARE, _KCAL_PER_GRAM["proteins"]),
        fats=_grams(calories, FAT_SHARE, _KCAL_PER_GRAM["fats"]),
        carbs=_grams(calories, CARB_SHARE, _KCAL_PER_GRAM["carbs"]),
    )
