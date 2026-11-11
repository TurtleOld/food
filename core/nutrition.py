"""КБЖУ в одном месте: поля, цвет точки, подпись в порядке показа и форматирование."""

from decimal import ROUND_HALF_UP, Decimal

NUTRITION_ROWS = (
    ("calories", "kcal", "ккал"),
    ("proteins", "p", "Б"),
    ("fats", "f", "Ж"),
    ("carbs", "c", "У"),
)

NUTRITION_FIELDS = [field for field, _dot, _label in NUTRITION_ROWS]

# Полные имена нужны там, где КБЖУ читает скринридер, а не глаз: в кольцах дня
# подпись «Б» без расшифровки бессмысленна.
NUTRITION_NAMES = {
    "calories": "Калории",
    "proteins": "Белки",
    "fats": "Жиры",
    "carbs": "Углеводы",
}


def whole(value: Decimal) -> str:
    """Округляет значение КБЖУ до целого и выводит строкой без учёта локали."""
    return str(int(value.to_integral_value(rounding=ROUND_HALF_UP)))


def one_decimal(value: Decimal) -> str:
    """Выводит значение КБЖУ с одним знаком и запятой как десятичным разделителем."""
    return str(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)).replace(".", ",")
