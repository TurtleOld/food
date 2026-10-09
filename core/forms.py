from decimal import Decimal
from typing import Any, cast

from django import forms
from django.db import transaction

from core.barcodes import normalize_barcode
from core.models import Barcode, DailyTarget, DiaryEntry, Product
from core.nutrition import NUTRITION_FIELDS, NUTRITION_ROWS


class DayField(forms.DateField):
    """Дата дня в формате дд/мм/гггг; ISO остаётся допустимым для скриптов и тестов."""

    input_formats = ["%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y"]


def day_widget() -> forms.DateInput:
    """Текстовое поле дня: нативный `type=date` показывает дату по языку браузера."""
    return forms.DateInput(
        attrs={"inputmode": "numeric", "placeholder": "дд/мм/гггг", "autocomplete": "off"},
        format="%d/%m/%Y",
    )


class ProductFieldsForm(forms.ModelForm):
    """Основа форм Продукта: поля КБЖУ для общего компонента `components/product_fields.html`."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.setdefault("class", "input")
        for name in NUTRITION_FIELDS:
            self.fields[name].widget.attrs.update(
                {"class": "input num", "step": "0.1", "min": "0", "inputmode": "decimal"}
            )

    barcode = forms.CharField(
        required=False,
        label="Цифры штрихкода",
        widget=forms.TextInput(
            attrs={
                "class": "input num",
                "inputmode": "numeric",
                "autocomplete": "off",
                "placeholder": "8, 12 или 13 цифр",
            }
        ),
    )
    transfer = forms.BooleanField(required=False)
    barcode_owner: Product | None = None

    def clean_barcode(self) -> str:
        raw = self.cleaned_data["barcode"]
        if not raw.strip():
            return ""
        code = normalize_barcode(raw)
        if code is None:
            raise forms.ValidationError(
                "Неверный штрихкод: нужно 8, 12 или 13 цифр и верная контрольная цифра"
            )
        return code

    def clean(self) -> dict[str, Any] | None:
        cleaned_data = super().clean()
        if cleaned_data is None:
            return cleaned_data
        code = cleaned_data.get("barcode")
        if code and not cleaned_data.get("transfer"):
            owner = Product.objects.filter(barcodes__code=code).exclude(pk=self.instance.pk).first()
            if owner is not None:
                self.barcode_owner = owner
                self.add_error("barcode", f"Код уже у «{owner.name}»")
        return cleaned_data

    def save(self, commit: bool = True) -> Product:
        # Код привязывается вместе с Продуктом, поэтому только при commit=True.
        with transaction.atomic():
            product = super().save(commit=commit)
            code = self.cleaned_data.get("barcode")
            if commit and code:
                Barcode.objects.update_or_create(code=code, defaults={"product": product})
        return product

    def draft_for_barcode_step(self) -> "ProductFieldsForm":
        """Форма с введёнными значениями без ошибок валидации, кроме ошибок поля штрихкода.

        Шаг «добавить код» не сохраняет Продукт, поэтому остальные поля не должны ругаться
        на незаполненность. Вызывается на связанной форме после `full_clean()`.
        """
        initial = {
            name: self.data[name]
            for name in self.fields
            if name in self.data and name not in ("barcode", "transfer")
        }
        draft = type(self)(initial=initial, instance=self.instance)
        draft.full_clean()
        draft.cleaned_data = {}
        for error in self.errors.get("barcode", []):
            draft.add_error("barcode", error)
        draft.barcode_owner = self.barcode_owner
        return draft

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
        field_classes = {"date": DayField}
        widgets = {
            "date": day_widget(),
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
        field_classes = {"date": DayField}
        widgets = {
            "date": day_widget(),
            "meal_type": forms.RadioSelect,
            "product": forms.HiddenInput,
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ChoiceField, self.fields["meal_type"]).choices = DiaryEntry.MealType.choices
