import datetime
from collections.abc import Iterable
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.base import ModelBase


class Product(models.Model):
    """Семейный каталог продуктов с пищевой ценностью на 100 г/мл."""

    class BaseUnit(models.TextChoices):
        GRAM = "g", "г"
        MILLILITER = "ml", "мл"

    name = models.CharField(max_length=200)
    base_unit = models.CharField(max_length=2, choices=BaseUnit.choices)
    calories = models.DecimalField(max_digits=7, decimal_places=1)
    proteins = models.DecimalField(max_digits=7, decimal_places=1)
    fats = models.DecimalField(max_digits=7, decimal_places=1)
    carbs = models.DecimalField(max_digits=7, decimal_places=1)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="authored_products",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Barcode(models.Model):
    """Штрихкод упаковки; уникален в Каталоге, поэтому скан находит ровно один Продукт.

    Хранится нормализованным: 8 цифр (EAN-8) или 13 (EAN-13, UPC-A с ведущим нулём).
    """

    code = models.CharField(max_length=13, unique=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="barcodes")

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return self.code


class DiaryEntry(models.Model):
    """Съеденный продукт с количеством, отнесённый к приёму пищи внутри дня."""

    class MealType(models.TextChoices):
        BREAKFAST = "breakfast", "Завтрак"
        SECOND_BREAKFAST = "second_breakfast", "Второй завтрак"
        LUNCH = "lunch", "Обед"
        AFTERNOON_SNACK = "afternoon_snack", "Полдник"
        DINNER = "dinner", "Ужин"

    MAX_AMOUNT = Decimal("10000")

    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="diary_entries",
    )
    date = models.DateField()
    meal_type = models.CharField(max_length=20, choices=MealType.choices)
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="diary_entries",
    )
    amount = models.DecimalField(max_digits=7, decimal_places=1)
    calories_snapshot = models.DecimalField(max_digits=7, decimal_places=1)
    proteins_snapshot = models.DecimalField(max_digits=7, decimal_places=1)
    fats_snapshot = models.DecimalField(max_digits=7, decimal_places=1)
    carbs_snapshot = models.DecimalField(max_digits=7, decimal_places=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name_plural = "diary entries"

    def __str__(self) -> str:
        return f"{self.product.name} × {self.amount}"

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        if self._state.adding:
            self.calories_snapshot = self.product.calories
            self.proteins_snapshot = self.product.proteins
            self.fats_snapshot = self.product.fats
            self.carbs_snapshot = self.product.carbs
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )

    @property
    def calories(self) -> Decimal:
        return self.amount / Decimal(100) * self.calories_snapshot

    @property
    def proteins(self) -> Decimal:
        return self.amount / Decimal(100) * self.proteins_snapshot

    @property
    def fats(self) -> Decimal:
        return self.amount / Decimal(100) * self.fats_snapshot

    @property
    def carbs(self) -> Decimal:
        return self.amount / Decimal(100) * self.carbs_snapshot


class Profile(models.Model):
    """Антропометрия Члена семьи; неполный Профиль допустим."""

    class Sex(models.TextChoices):
        MALE = "male", "Мужской"
        FEMALE = "female", "Женский"

    class Activity(models.TextChoices):
        SEDENTARY = "sedentary", "Сидячий образ жизни"
        MODERATE = "moderate", "Умеренная активность"
        HIGH = "high", "Высокая активность"

    MIN_HEIGHT_CM = 50
    MAX_HEIGHT_CM = 250

    member = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    height_cm = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[
            MinValueValidator(MIN_HEIGHT_CM, f"Рост должен быть не меньше {MIN_HEIGHT_CM} см"),
            MaxValueValidator(MAX_HEIGHT_CM, f"Рост должен быть не больше {MAX_HEIGHT_CM} см"),
        ],
    )
    birth_date = models.DateField(null=True, blank=True)
    sex = models.CharField(max_length=10, choices=Sex.choices, blank=True)
    activity = models.CharField(max_length=20, choices=Activity.choices, blank=True)

    def __str__(self) -> str:
        return f"Профиль {self.member.username}"

    @property
    def is_complete(self) -> bool:
        """Все поля, без которых не считается расчёт, заполнены."""
        return bool(self.height_cm and self.birth_date and self.sex and self.activity)

    def age_on(self, date: datetime.date) -> int | None:
        """Возраст в полных годах на дату `date`; без даты рождения — None."""
        if self.birth_date is None:
            return None
        return (
            date.year
            - self.birth_date.year
            - ((date.month, date.day) < (self.birth_date.month, self.birth_date.day))
        )


class WeightMeasurement(models.Model):
    """Вес Члена семьи на дату; ИМТ и расчёт цели берут последний Замер."""

    MIN_WEIGHT_KG = Decimal("20")
    MAX_WEIGHT_KG = Decimal("400")

    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="weight_measurements",
    )
    date = models.DateField()
    weight_kg = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        validators=[
            MinValueValidator(MIN_WEIGHT_KG, f"Вес должен быть не меньше {MIN_WEIGHT_KG:g} кг"),
            MaxValueValidator(MAX_WEIGHT_KG, f"Вес должен быть не больше {MAX_WEIGHT_KG:g} кг"),
        ],
    )

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self) -> str:
        return f"{self.date:%d/%m/%Y}: {self.weight_kg} кг"


class DailyTarget(models.Model):
    """Суточная цель по КБЖУ, заданная вручную и своя у каждого члена семьи."""

    member = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="daily_target",
    )
    calories = models.DecimalField(max_digits=7, decimal_places=1)
    proteins = models.DecimalField(max_digits=7, decimal_places=1)
    fats = models.DecimalField(max_digits=7, decimal_places=1)
    carbs = models.DecimalField(max_digits=7, decimal_places=1)

    def __str__(self) -> str:
        return f"Цель {self.member.username}"
