from typing import Any, cast

from django import forms

from assistant.models import LLMSettings, Proposal
from core.forms import DayField, day_widget
from core.models import DiaryEntry, Product
from core.nutrition import NUTRITION_FIELDS


class LLMSettingsForm(forms.ModelForm):
    """Форма администратора: пустое поле ключа сохраняет уже записанный ключ."""

    class Meta:
        model = LLMSettings
        fields = [
            "base_url",
            "model",
            "api_key",
            "monthly_budget_rub",
            "input_price_rub",
            "cached_price_rub",
            "output_price_rub",
            "max_input_tokens",
            "max_output_tokens",
            "timeout_seconds",
        ]
        labels = {
            "base_url": "Base URL провайдера",
            "model": "Модель",
            "api_key": "Ключ API",
            "monthly_budget_rub": "Месячный бюджет семьи, ₽",
            "input_price_rub": "Входные токены, ₽ за миллион",
            "cached_price_rub": "Кэшированные входные токены, ₽ за миллион",
            "output_price_rub": "Выходные токены, ₽ за миллион",
            "max_input_tokens": "Лимит входных токенов",
            "max_output_tokens": "Лимит выходных токенов",
            "timeout_seconds": "Таймаут запроса, секунд",
        }
        help_texts = {
            "base_url": "Например, https://api.deepseek.com/v1 — без /chat/completions.",
            "api_key": (
                "Оставьте пустым, чтобы не менять сохранённый ключ. "
                "Ключ виден только администратору."
            ),
            "input_price_rub": (
                "Пусто — тариф не задан и ассистент выключен; 0 — бесплатный тариф."
            ),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["base_url"].required = True
        self.fields["api_key"].widget = forms.PasswordInput(render_value=False)
        for field in self.fields.values():
            css = "input"
            if isinstance(field, (forms.DecimalField, forms.IntegerField)):
                css += " num"
            field.widget.attrs.setdefault("class", css)
        self.fields["api_key"].widget.attrs["autocomplete"] = "off"

    def clean_api_key(self) -> str:
        key = self.cleaned_data["api_key"].strip()
        if key:
            return key
        if self.instance.pk and self.instance.api_key:
            return self.instance.api_key
        raise forms.ValidationError("Укажите ключ провайдера")


class ProposalForm(forms.ModelForm):
    """Правка карточки Предложения: Продукт, данные нового Продукта, количество, день, приём."""

    product = forms.ModelChoiceField(
        queryset=Product.objects.all(),
        required=False,
        label="Продукт Каталога",
        empty_label="Новый продукт",
    )

    class Meta:
        model = Proposal
        fields = [
            "product",
            "new_name",
            "new_base_unit",
            "new_calories",
            "new_proteins",
            "new_fats",
            "new_carbs",
            "amount",
            "date",
            "meal_type",
        ]
        field_classes = {"date": DayField}
        widgets = {
            "date": day_widget(),
            "meal_type": forms.RadioSelect,
            "new_base_unit": forms.RadioSelect,
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.product_only = self.instance.kind == Proposal.Kind.PRODUCT
        cast(forms.ModelChoiceField, self.fields["product"]).queryset = Product.objects.order_by(
            "name"
        )
        cast(forms.ChoiceField, self.fields["meal_type"]).choices = DiaryEntry.MealType.choices
        self.fields["product"].widget.attrs.setdefault("class", "input")
        self.fields["new_name"].widget.attrs.setdefault("class", "input")
        self.fields["amount"].widget.attrs.update(
            {"class": "input num", "step": "0.1", "min": "0.1", "inputmode": "decimal"}
        )
        for name in NUTRITION_FIELDS:
            self.fields[f"new_{name}"].widget.attrs.update(
                {"class": "input num", "step": "0.1", "min": "0", "inputmode": "decimal"}
            )
        if self.product_only:
            # У Предложения Продукта нет Записи: поля дня и количества скрыты.
            for name in ("product", "amount", "date", "meal_type"):
                self.fields[name].required = False
                self.fields[name].widget = forms.HiddenInput()

    def clean_amount(self) -> Any:
        if self.product_only:
            return None
        amount = self.cleaned_data.get("amount")
        if amount is None:
            return None
        if amount <= 0:
            raise forms.ValidationError("Количество должно быть больше нуля")
        if amount > DiaryEntry.MAX_AMOUNT:
            raise forms.ValidationError(
                f"Количество не может быть больше {DiaryEntry.MAX_AMOUNT:g}"
            )
        return amount

    def _clean_new_product(self, cleaned_data: dict[str, Any]) -> None:
        new_name = str(cleaned_data.get("new_name") or "").strip()
        if not new_name:
            self.add_error("new_name", "Укажите название нового Продукта")
        else:
            cleaned_data["new_name"] = new_name
        if cleaned_data.get("new_base_unit") not in Product.BaseUnit.values:
            self.add_error("new_base_unit", "Выберите граммы или миллилитры")
        for name in NUTRITION_FIELDS:
            value = cleaned_data.get(f"new_{name}")
            if value is None:
                self.add_error(f"new_{name}", "Заполните КБЖУ нового Продукта")
            elif value < 0:
                self.add_error(f"new_{name}", "Значение не может быть отрицательным")

    def clean(self) -> dict[str, Any] | None:
        cleaned_data = super().clean()
        if cleaned_data is None:
            return cleaned_data
        if self.product_only:
            cleaned_data["product"] = None
            cleaned_data["amount"] = None
            cleaned_data["date"] = None
            cleaned_data["meal_type"] = ""
            self._clean_new_product(cleaned_data)
            return cleaned_data

        if cleaned_data.get("amount") is None:
            self.add_error("amount", "Укажите количество")
        if cleaned_data.get("date") is None:
            self.add_error("date", "Укажите дату")
        if not cleaned_data.get("meal_type"):
            self.add_error("meal_type", "Выберите Приём пищи")
        if cleaned_data.get("product") is None:
            self._clean_new_product(cleaned_data)
        else:
            # Выбран существующий Продукт: недописанные поля нового Продукта не сохраняем.
            cleaned_data["new_name"] = ""
            cleaned_data["new_base_unit"] = ""
            for name in NUTRITION_FIELDS:
                cleaned_data[f"new_{name}"] = None
        return cleaned_data
