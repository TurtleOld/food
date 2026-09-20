from django import forms

from core.models import Product

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
