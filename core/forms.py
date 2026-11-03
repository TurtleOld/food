import base64
import binascii
import datetime
from decimal import Decimal
from typing import Any, cast

from django import forms
from django.db import transaction
from django.db.models import TextChoices
from django.utils import timezone

from core.barcodes import normalize_barcode
from core.models import (
    Barcode,
    DailyTarget,
    DiaryEntry,
    Product,
    Profile,
    PushSettings,
    WeightMeasurement,
)
from core.nutrition import NUTRITION_FIELDS, NUTRITION_ROWS
from core.profile import MAX_ADJUSTMENT_KCAL, MIN_ADJUSTMENT_KCAL
from core.push import is_allowed_push_endpoint


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


class ProfileForm(forms.ModelForm):
    """Форма антропометрии Профиля; любое поле можно оставить пустым."""

    class Meta:
        model = Profile
        fields = ["height_cm", "birth_date", "sex", "activity"]
        field_classes = {"birth_date": DayField}
        widgets = {
            "birth_date": day_widget(),
            "height_cm": forms.NumberInput(
                attrs={
                    "class": "input num",
                    "inputmode": "numeric",
                    "min": Profile.MIN_HEIGHT_CM,
                    "max": Profile.MAX_HEIGHT_CM,
                }
            ),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["birth_date"].widget.attrs.update(
            {"class": "input num", "inputmode": "numeric"}
        )
        self.fields["sex"].widget.attrs.setdefault("class", "input")
        self.fields["activity"].widget.attrs.setdefault("class", "input")
        cast(forms.ChoiceField, self.fields["sex"]).choices = [
            ("", "Не указан"),
            *Profile.Sex.choices,
        ]
        cast(forms.ChoiceField, self.fields["activity"]).choices = [
            ("", "Не указана"),
            *Profile.Activity.choices,
        ]

    def clean_birth_date(self) -> datetime.date | None:
        """Не принимает будущую или неправдоподобно давнюю дату рождения."""
        birth_date = self.cleaned_data["birth_date"]
        if birth_date is None:
            return None
        if birth_date > timezone.localdate():
            raise forms.ValidationError("Дата рождения не может быть в будущем")
        if birth_date < datetime.date(1900, 1, 1):
            raise forms.ValidationError("Дата рождения не может быть раньше 1900 года")
        return birth_date


class WeightMeasurementForm(forms.ModelForm):
    """Форма нового Замера веса на дату."""

    class Meta:
        model = WeightMeasurement
        fields = ["date", "weight_kg"]
        field_classes = {"date": DayField}
        widgets = {
            "date": day_widget(),
            "weight_kg": forms.NumberInput(
                attrs={
                    "class": "input num",
                    "inputmode": "decimal",
                    "step": "0.1",
                    "min": WeightMeasurement.MIN_WEIGHT_KG,
                    "max": WeightMeasurement.MAX_WEIGHT_KG,
                }
            ),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["date"].widget.attrs.update({"class": "input num", "inputmode": "numeric"})

    def clean_date(self) -> datetime.date:
        """Не принимает будущую или неправдоподобно давнюю дату Замера."""
        date = self.cleaned_data["date"]
        if date > timezone.localdate():
            raise forms.ValidationError("Дата Замера не может быть в будущем")
        if date < datetime.date(1900, 1, 1):
            raise forms.ValidationError("Дата Замера не может быть раньше 1900 года")
        return date


class TargetAdjustmentForm(forms.Form):
    """Ручная поправка к расчёту поддержания, в ккал."""

    adjustment = forms.DecimalField(
        required=False,
        max_digits=4,
        decimal_places=0,
        min_value=MIN_ADJUSTMENT_KCAL,
        max_value=MAX_ADJUSTMENT_KCAL,
        initial=Decimal(0),
        label="Поправка, ккал",
        widget=forms.NumberInput(
            attrs={
                "class": "input num",
                "inputmode": "numeric",
                "step": "10",
                "min": MIN_ADJUSTMENT_KCAL,
                "max": MAX_ADJUSTMENT_KCAL,
            }
        ),
    )

    def clean_adjustment(self) -> Decimal:
        """Пустая поправка означает поддержание."""
        return self.cleaned_data.get("adjustment") or Decimal(0)


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


class DiaryCopyForm(forms.Form):
    """Копирование Записей дневника: день, Приём пищи или одна Запись в другой день."""

    class Scope(TextChoices):
        DAY = "day", "Весь день"
        MEAL = "meal", "Приём пищи"
        ENTRY = "entry", "Запись"

    scope = forms.ChoiceField(choices=Scope.choices, widget=forms.HiddenInput)
    source_date = DayField(label="Исходный день", required=False, widget=day_widget())
    target_date = DayField(label="Целевой день", widget=day_widget())
    meal_type = forms.ChoiceField(
        choices=DiaryEntry.MealType.choices, required=False, widget=forms.HiddenInput
    )
    entry = forms.ModelChoiceField(
        queryset=DiaryEntry.objects.none(), required=False, widget=forms.HiddenInput
    )
    target_meal_type = forms.ChoiceField(
        choices=DiaryEntry.MealType.choices, required=False, widget=forms.RadioSelect
    )
    operation = forms.CharField(max_length=64, widget=forms.HiddenInput)

    def __init__(self, *args: Any, member: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        cast(forms.ModelChoiceField, self.fields["entry"]).queryset = DiaryEntry.objects.filter(
            member_id=member.pk
        )

    def clean(self) -> dict[str, Any] | None:
        cleaned_data = super().clean()
        if cleaned_data is None:
            return cleaned_data
        scope = cleaned_data.get("scope")
        if scope == self.Scope.ENTRY:
            entry = cleaned_data.get("entry")
            if entry is None:
                self.add_error("entry", "Запись не найдена")
            else:
                cleaned_data["source_date"] = entry.date
                cleaned_data["target_meal_type"] = (
                    cleaned_data.get("target_meal_type") or entry.meal_type
                )
        elif scope in (self.Scope.DAY, self.Scope.MEAL):
            if cleaned_data.get("source_date") is None:
                self.add_error("source_date", "Укажите исходный день")
            if scope == self.Scope.MEAL and not cleaned_data.get("meal_type"):
                self.add_error("meal_type", "Выберите приём пищи")
        return cleaned_data


def _base64url_bytes(value: str) -> bytes:
    """Декодирует base64url с необязательным padding или объясняет, что ключ неверный."""
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except (ValueError, binascii.Error):
        raise forms.ValidationError("Ключ должен быть в base64url") from None


class PushSubscriptionForm(forms.Form):
    """Подписка браузера: endpoint и ключи шифрования из `PushSubscription.toJSON()`."""

    endpoint = forms.URLField(max_length=2048)
    p256dh = forms.CharField(max_length=200)
    auth = forms.CharField(max_length=100)

    def clean_endpoint(self) -> str:
        """Не принимает endpoint не из известного push-сервиса."""
        endpoint = self.cleaned_data["endpoint"]
        if not is_allowed_push_endpoint(endpoint):
            raise forms.ValidationError("Неизвестный сервис уведомлений")
        return endpoint

    def clean_p256dh(self) -> str:
        """Публичный ключ устройства — несжатая точка P-256, ровно 65 байт."""
        p256dh = self.cleaned_data["p256dh"]
        if len(_base64url_bytes(p256dh)) != 65:
            raise forms.ValidationError("Неверный размер ключа устройства")
        return p256dh

    def clean_auth(self) -> str:
        """Секрет подписки — ровно 16 байт."""
        auth = self.cleaned_data["auth"]
        if len(_base64url_bytes(auth)) != 16:
            raise forms.ValidationError("Неверный размер секрета подписки")
        return auth


class PushUnsubscribeForm(forms.Form):
    """Отписка устройства; без endpoint удаляет все подписки Члена семьи."""

    endpoint = forms.URLField(max_length=2048, required=False)


class PushSettingsForm(forms.ModelForm):
    """VAPID-настройки администратора; пустой приватный ключ сохраняет записанный."""

    class Meta:
        model = PushSettings
        fields = ["vapid_public_key", "vapid_private_key", "vapid_subject"]
        labels = {
            "vapid_public_key": "VAPID public key",
            "vapid_private_key": "VAPID private key",
            "vapid_subject": "VAPID subject",
        }
        help_texts = {
            "vapid_public_key": "Base64url-ключ, который браузер получает при подписке.",
            "vapid_private_key": (
                "PEM или base64 DER. Оставьте пустым, чтобы не менять сохранённый ключ."
            ),
            "vapid_subject": "Контакт администратора: mailto:you@example.com или HTTPS URL.",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "input")
        self.fields["vapid_private_key"].required = False
        self.fields["vapid_private_key"].widget = forms.PasswordInput(render_value=False)
        self.fields["vapid_private_key"].widget.attrs["autocomplete"] = "off"

    def clean_vapid_public_key(self) -> str:
        """Публичный VAPID-ключ — тоже несжатая точка P-256."""
        key = self.cleaned_data["vapid_public_key"].strip()
        if key and len(_base64url_bytes(key)) != 65:
            raise forms.ValidationError("Неверный размер публичного ключа")
        return key

    def clean_vapid_private_key(self) -> str:
        """Пустое поле не стирает уже сохранённый приватный ключ."""
        key = self.cleaned_data["vapid_private_key"].strip()
        if key:
            return key
        if self.instance.pk and self.instance.vapid_private_key:
            return self.instance.vapid_private_key
        return ""

    def clean_vapid_subject(self) -> str:
        return self.cleaned_data["vapid_subject"].strip()
