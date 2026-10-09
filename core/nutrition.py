"""КБЖУ в одном месте: поля, цвет точки и подпись в порядке показа."""

NUTRITION_ROWS = (
    ("calories", "kcal", "ккал"),
    ("proteins", "p", "Б"),
    ("fats", "f", "Ж"),
    ("carbs", "c", "У"),
)

NUTRITION_FIELDS = [field for field, _dot, _label in NUTRITION_ROWS]
