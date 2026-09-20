from decimal import Decimal
from typing import Any

from django import forms

from core.models import DailyTarget, DiaryEntry, Product

NUTRITION_FIELDS = ["calories", "proteins", "fats", "carbs"]


class ProductCreateForm(forms.ModelForm):
    """Форма создания продукта: базовая единица задаётся один раз."""

    class Meta:
        model = Product
        fields = ["name", "base_unit", *NUTRITION_FIELDS]


class ProductEditForm(forms.ModelForm):
    """Форма правки продукта: базовая единица недоступна для изменения."""

    class Meta:
        model = Product
        fields = ["name", *NUTRITION_FIELDS]


class DiaryEntryForm(forms.ModelForm):
    """Форма создания записи дневника: приём пищи, продукт и количество."""

    class Meta:
        model = DiaryEntry
        fields = ["meal_type", "product", "amount"]

    def clean_amount(self) -> Decimal:
        """Отклоняет количество вне диапазона (0; MAX_AMOUNT]."""
        amount = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("Количество должно быть больше нуля")
        if amount > DiaryEntry.MAX_AMOUNT:
            raise forms.ValidationError(
                f"Количество не может быть больше {DiaryEntry.MAX_AMOUNT:g}"
            )
        return amount


class DiaryEntryEditForm(DiaryEntryForm):
    """Форма правки записи дневника: количество, приём пищи и дата."""

    class Meta:
        model = DiaryEntry
        fields = ["date", "meal_type", "amount"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}


class DailyTargetForm(forms.ModelForm):
    """Форма суточной цели по КБЖУ, своей у каждого члена семьи."""

    class Meta:
        model = DailyTarget
        fields = NUTRITION_FIELDS

    def clean(self) -> dict[str, Any] | None:
        cleaned_data = super().clean()
        if cleaned_data is None:
            return cleaned_data
        for field in NUTRITION_FIELDS:
            value = cleaned_data.get(field)
            if value is not None and value <= 0:
                self.add_error(field, "Значение должно быть больше нуля")
        return cleaned_data
