from collections.abc import Iterable
from decimal import Decimal

from django.conf import settings
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
