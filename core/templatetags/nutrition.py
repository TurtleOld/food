from decimal import ROUND_HALF_UP, Decimal

from django import template

register = template.Library()


@register.filter
def whole(value: Decimal) -> str:
    """Round a nutrition value to a locale-independent integer string."""
    return str(int(value.to_integral_value(rounding=ROUND_HALF_UP)))


@register.filter
def one_decimal(value: Decimal) -> str:
    """Format a nutrition value with exactly one, locale-independent decimal place."""
    return str(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


@register.filter
def sub(value: Decimal, other: Decimal) -> Decimal:
    """Subtract `other` from `value`."""
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
