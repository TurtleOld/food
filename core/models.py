from django.conf import settings
from django.db import models


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
