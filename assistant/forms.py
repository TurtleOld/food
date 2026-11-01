from typing import Any

from django import forms

from assistant.models import LLMSettings


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
            "input_price_rub": "Пусто или 0 — тариф не задан, ассистент выключен.",
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
