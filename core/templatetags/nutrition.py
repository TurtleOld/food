from decimal import Decimal

from django import template

from core.nutrition import one_decimal, whole

register = template.Library()
register.filter(whole)
register.filter(one_decimal)


@register.filter
def sub(value: Decimal, other: Decimal) -> Decimal:
    """Вычитает `other` из `value`."""
    return value - other


@register.filter
def plural(count: int, forms: str) -> str:
    """Выбирает русскую форму слова по числу: `forms` — «один,два,пять» через запятую."""
    one, few, many = forms.split(",")
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many
