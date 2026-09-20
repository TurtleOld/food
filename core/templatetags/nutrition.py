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
