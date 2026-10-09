"""Шлюз Open Food Facts: данные для формы Продукта по полному коду, которого нет в Каталоге."""

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache

API_URL = "https://world.openfoodfacts.org/api/v3/product/"
PRODUCT_URL = "https://world.openfoodfacts.org/product/"
FIELDS = "product_name,brands,nutriments,nutrition_data_per,product_quantity_unit"
TIMEOUT_SECONDS = 4
CACHE_SECONDS = 600
APP_VERSION = "0.1"

_UNITS = {"100g": "g", "100ml": "ml"}
_QUANTITY_UNITS = {"g": "g", "kg": "g", "ml": "ml", "cl": "ml", "l": "ml"}


class Outcome(Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    FAILED = "failed"


@dataclass(frozen=True)
class OffLookup:
    """Исход обращения к Open Food Facts.

    У `FOUND` заполнены `name`, `url` и разобранные значения на 100 г / мл; отсутствующее
    значение — `None`. `base_unit` — `g`/`ml` или `None`, если OFF не сказал, на что КБЖУ.
    `unit_hint` — `g`/`ml` по единице количества упаковки, только когда `base_unit` не ясен:
    это подсказка для человека, а не предвыбор.
    """

    outcome: Outcome
    name: str = ""
    base_unit: str | None = None
    calories: Decimal | None = None
    proteins: Decimal | None = None
    fats: Decimal | None = None
    carbs: Decimal | None = None
    url: str = ""
    unit_hint: str | None = None

    @property
    def found(self) -> bool:
        return self.outcome is Outcome.FOUND

    @property
    def failed(self) -> bool:
        return self.outcome is Outcome.FAILED

    @property
    def missing(self) -> list[str]:
        """Имена полей КБЖУ, которых нет в данных OFF."""
        values = {
            "calories": self.calories,
            "proteins": self.proteins,
            "fats": self.fats,
            "carbs": self.carbs,
        }
        return [name for name, value in values.items() if value is None]

    @property
    def is_complete(self) -> bool:
        return not self.missing


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if not number.is_finite() or number < 0:
        return None
    return number.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _name(product: dict[str, Any]) -> str:
    title = str(product.get("product_name") or "").strip()
    brand = str(product.get("brands") or "").split(",")[0].strip()
    if brand and not title.lower().startswith(brand.lower()):
        return f"{brand} {title}".strip()
    return title


def _parse(code: str, product: dict[str, Any]) -> OffLookup:
    nutriments = product.get("nutriments")
    if not isinstance(nutriments, dict):
        nutriments = {}
    # `energy_100g` в кДж, поэтому ккал берутся только из `energy-kcal_100g`.
    fats = nutriments.get("fat_100g", nutriments.get("fats_100g"))
    base_unit = _UNITS.get(str(product.get("nutrition_data_per")))
    unit_hint = (
        None if base_unit else _QUANTITY_UNITS.get(str(product.get("product_quantity_unit")))
    )
    return OffLookup(
        outcome=Outcome.FOUND,
        name=_name(product),
        base_unit=base_unit,
        unit_hint=unit_hint,
        calories=_decimal(nutriments.get("energy-kcal_100g")),
        proteins=_decimal(nutriments.get("proteins_100g")),
        fats=_decimal(fats),
        carbs=_decimal(nutriments.get("carbohydrates_100g")),
        url=f"{PRODUCT_URL}{code}",
    )


def _fetch(code: str) -> OffLookup:
    request = Request(
        f"{API_URL}{code}?{urlencode({'fields': FIELDS})}",
        headers={"User-Agent": f"food-tracker/{APP_VERSION} ({settings.OFF_CONTACT})"},
    )
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            payload = json.loads(response.read())
    except HTTPError as error:
        # Только JSON `product_not_found` — честный промах; прочие 404 (прокси, HTML) — сбой.
        try:
            body = json.loads(error.read())
            missing = error.code == 404 and body["result"]["id"] == "product_not_found"
        except (ValueError, KeyError, TypeError):
            missing = False
        return OffLookup(Outcome.NOT_FOUND if missing else Outcome.FAILED)
    except (OSError, ValueError):
        return OffLookup(Outcome.FAILED)
    product = payload.get("product") if isinstance(payload, dict) else None
    if not isinstance(product, dict):
        return OffLookup(Outcome.FAILED)
    return _parse(code, product)


def lookup(code: str) -> OffLookup:
    """Спрашивает OFF про нормализованный код; находки и промахи кэшируются, сбои нет."""
    key = f"off:{code}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    result = _fetch(code)
    if result.outcome is not Outcome.FAILED:
        cache.set(key, result, CACHE_SECONDS)
    return result
