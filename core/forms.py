from decimal import Decimal
from typing import Any, cast

from django import forms

from core.models import DailyTarget, DiaryEntry, Product

NUTRITION_FIELDS = ["calories", "proteins", "fats", "carbs"]


NUTRITION_ROWS = [
    ("calories", "kcal", "ккал"),
    ("proteins", "p", "Б"),
    ("fats", "f", "Ж"),
    ("carbs", "c", "У"),
]


class ProductFieldsForm(forms.ModelForm):
    """Основа форм Продукта: поля КБЖУ для общего компонента `components/product_fields.html`."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for name in NUTRITION_FIELDS:
            self.fields[name].widget.attrs.update(
                {"class": "input num", "step": "0.1", "min": "0", "inputmode": "decimal"}
            )

    @property
    def nutrition_rows(self) -> list[tuple[Any, str, str]]:
        """Поля КБЖУ с цветом точки и подписью в порядке показа."""
        return [(self[name], dot, label) for name, dot, label in NUTRITION_ROWS]


class ProductCreateForm(ProductFieldsForm):
    """Форма создания продукта: базовая единица задаётся один раз."""

    class Meta:
        model = Product
        fields = ["name", "base_unit", *NUTRITION_FIELDS]
        widgets = {"base_unit": forms.RadioSelect}


class ProductEditForm(ProductFieldsForm):
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
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "meal_type": forms.RadioSelect,
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # Без пустого варианта: Приём пищи у записи всегда выбран.
        cast(forms.ChoiceField, self.fields["meal_type"]).choices = DiaryEntry.MealType.choices


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


class DiaryEntrySheetForm(DiaryEntryForm):
    """Шаг количества шторки: продукт задан заранее, дата и приём правятся на шаге."""

    class Meta:
        model = DiaryEntry
        fields = ["date", "meal_type", "product", "amount"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "meal_type": forms.RadioSelect,
            "product": forms.HiddenInput,
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["meal_type"]).choices = DiaryEntry.MealType.choices
