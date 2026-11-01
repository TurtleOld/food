"""Настройки провайдера, резервы бюджета, история стоимости и Предложения."""

from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from core.models import DiaryEntry, Product


class LLMSettings(models.Model):
    """Единственные на семью настройки OpenAI-совместимого провайдера и бюджета.

    Хранятся в БД, а не в .env: их меняет администратор через UI. Пустые значения
    означают, что ассистент ещё не настроен и обращения к модели запрещены.
    """

    SINGLETON_PK = 1

    base_url = models.URLField(max_length=500, blank=True)
    model = models.CharField(max_length=200, default="deepseek-flash")
    api_key = models.CharField(max_length=500, blank=True)
    monthly_budget_rub = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(Decimal("0"))],
    )
    input_price_rub = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    cached_price_rub = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    output_price_rub = models.DecimalField(
        max_digits=12,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    max_input_tokens = models.PositiveIntegerField(default=12000)
    max_output_tokens = models.PositiveIntegerField(default=1500)
    timeout_seconds = models.PositiveSmallIntegerField(
        default=30, validators=[MaxValueValidator(120)]
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "LLM settings"

    def __str__(self) -> str:
        return self.model or "Провайдер не настроен"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Не даёт создать вторую строку: все правки идут в строку с постоянным pk."""
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)

    @classmethod
    def current(cls) -> "LLMSettings":
        """Возвращает единственную строку настроек, создавая её при первом обращении.

        `get_or_create` переживает гонку двух первых форм: проигравший INSERT получает
        уже созданную строку, а не 500 и не вторую конфигурацию.
        """
        settings, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return settings

    @property
    def is_configured(self) -> bool:
        prices = (self.input_price_rub, self.cached_price_rub, self.output_price_rub)
        return bool(
            self.base_url
            and self.api_key
            and self.model
            and self.monthly_budget_rub > 0
            and all(price is not None for price in prices)
            and self.max_input_tokens > 0
            and self.max_output_tokens > 0
            and self.timeout_seconds > 0
        )

    @property
    def masked_api_key(self) -> str:
        """Ключ для экрана настроек: последние четыре знака, остальное скрыто."""
        if len(self.api_key) <= 4:
            return "•" * len(self.api_key)
        return f"••••{self.api_key[-4:]}"


class BudgetReservation(models.Model):
    """Деньги, зарезервированные под один запрос до обращения к провайдеру.

    Тарифы и модель копируются в резерв в момент запроса: последующая правка
    настроек не меняет стоимость уже начатого обращения.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает"
        SETTLED = "settled", "Учтён"
        RELEASED = "released", "Освобождён"

    request_id = models.CharField(max_length=64, unique=True)
    amount_rub = models.DecimalField(max_digits=14, decimal_places=6)
    model = models.CharField(max_length=200)
    input_price_rub = models.DecimalField(max_digits=12, decimal_places=4)
    cached_price_rub = models.DecimalField(max_digits=12, decimal_places=4)
    output_price_rub = models.DecimalField(max_digits=12, decimal_places=4)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    billing_month = models.DateField(db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.request_id}: {self.amount_rub} ₽ ({self.get_status_display()})"


class Proposal(models.Model):
    """Подготовленное Ассистентом Предложение: новая Запись и/или новый Продукт.

    Хранится до решения Члена семьи и сама ничего не меняет. Подтверждение один раз
    создаёт Запись и, если Продукт новый, сам Продукт; отклонение данных не создаёт.
    Предложение Продукта только добавляет Продукт в Каталог, без Записи.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает решения"
        CONFIRMED = "confirmed", "Подтверждено"
        REJECTED = "rejected", "Отклонено"

    class Kind(models.TextChoices):
        ENTRY = "entry", "Запись"
        PRODUCT = "product", "Продукт"

    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="proposals",
    )
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.ENTRY)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    date = models.DateField(null=True, blank=True)
    meal_type = models.CharField(max_length=20, choices=DiaryEntry.MealType.choices, blank=True)
    amount = models.DecimalField(max_digits=7, decimal_places=1, null=True, blank=True)
    product = models.ForeignKey(
        Product,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="proposals",
    )
    new_name = models.CharField(max_length=200, blank=True)
    new_base_unit = models.CharField(max_length=2, choices=Product.BaseUnit.choices, blank=True)
    new_calories = models.DecimalField(max_digits=7, decimal_places=1, null=True, blank=True)
    new_proteins = models.DecimalField(max_digits=7, decimal_places=1, null=True, blank=True)
    new_fats = models.DecimalField(max_digits=7, decimal_places=1, null=True, blank=True)
    new_carbs = models.DecimalField(max_digits=7, decimal_places=1, null=True, blank=True)
    entry = models.OneToOneField(
        DiaryEntry,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="proposal",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        name = self.product.name if self.product else self.new_name
        return f"{name}: {self.get_status_display()}"

    @property
    def similar(self) -> list[Product]:
        """Похожие Продукты Каталога для выбора; для существующего Продукта — пусто."""
        if self.product_id is not None:
            return []
        from assistant.proposals import similar_products

        return similar_products(self.new_name)


class UsageRecord(models.Model):
    """Фактический usage и стоимость завершённого запроса; строки не редактируются."""

    reservation = models.OneToOneField(
        BudgetReservation,
        on_delete=models.PROTECT,
        related_name="usage",
    )
    model = models.CharField(max_length=200)
    input_tokens = models.PositiveIntegerField()
    cached_input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField()
    input_price_rub = models.DecimalField(max_digits=12, decimal_places=4)
    cached_price_rub = models.DecimalField(max_digits=12, decimal_places=4)
    output_price_rub = models.DecimalField(max_digits=12, decimal_places=4)
    cost_rub = models.DecimalField(max_digits=14, decimal_places=6)
    billing_month = models.DateField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.model}: {self.cost_rub} ₽"
