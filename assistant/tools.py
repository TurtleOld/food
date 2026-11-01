"""Инструменты Ассистента: схема для модели, чтение данных и подготовка Предложений.

Личные данные (Дневник, Профиль, цель) читаются только у переданного Члена семьи;
Каталог общий. Подготовка Предложения ничего не сохраняет в Дневник.
"""

import datetime
import inspect
import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Callable

from assistant.models import Proposal
from assistant.proposals import (
    ProposalError,
    create_product_proposal,
    create_proposal,
    similar_products,
)
from core.barcodes import normalize_barcode
from core.diary import search_products
from core.models import Barcode, DailyTarget, DiaryEntry, Product
from core.off import OffLookup
from core.off import lookup as off_lookup
from core.profile import profile_of, target_suggestion

MAX_CATALOG_RESULTS = 20
MAX_DIARY_DAYS = 31

_MEAL_VALUES = list(DiaryEntry.MealType.values)
_MEAL_LABELS = dict(DiaryEntry.MealType.choices)


class ToolArgumentError(ValueError):
    """Аргументы инструмента не являются JSON-объектом."""


def _product(product: Product) -> dict[str, Any]:
    return {
        "id": product.pk,
        "name": product.name,
        "base_unit": product.base_unit,
        "calories": str(product.calories),
        "proteins": str(product.proteins),
        "fats": str(product.fats),
        "carbs": str(product.carbs),
    }


def _off(lookup: OffLookup) -> dict[str, Any]:
    return {
        "outcome": lookup.outcome.value,
        "name": lookup.name,
        "base_unit": lookup.base_unit,
        "calories": None if lookup.calories is None else str(lookup.calories),
        "proteins": None if lookup.proteins is None else str(lookup.proteins),
        "fats": None if lookup.fats is None else str(lookup.fats),
        "carbs": None if lookup.carbs is None else str(lookup.carbs),
        "missing": lookup.missing,
        "url": lookup.url,
    }


def _iso_date(raw: Any) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(raw).strip())
    except ValueError:
        return None


def _target(target: DailyTarget | None) -> dict[str, Any] | None:
    if target is None:
        return None
    return {
        "calories": str(target.calories),
        "proteins": str(target.proteins),
        "fats": str(target.fats),
        "carbs": str(target.carbs),
    }


def _profile(member: Any) -> dict[str, Any] | None:
    profile = profile_of(member)
    if profile is None:
        return None
    birth_date = profile.birth_date
    return {
        "height_cm": profile.height_cm,
        "birth_date": birth_date.isoformat() if birth_date else None,
        "sex": profile.sex,
        "activity": profile.activity,
        "is_complete": bool(profile.is_complete),
    }


def _suggestion(member: Any) -> dict[str, Any] | None:
    suggestion = target_suggestion(member)
    if suggestion is None:
        return None
    return {
        field: str(getattr(suggestion, field))
        for field in (
            "maintenance_kcal",
            "adjustment_kcal",
            "calories",
            "proteins",
            "fats",
            "carbs",
        )
    }


def _proposal(proposal: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": proposal.pk,
        "kind": proposal.kind,
        "status": proposal.status,
    }
    if proposal.kind == Proposal.Kind.PRODUCT:
        payload["new_product"] = {
            "name": proposal.new_name,
            "base_unit": proposal.new_base_unit,
            "calories": str(proposal.new_calories),
            "proteins": str(proposal.new_proteins),
            "fats": str(proposal.new_fats),
            "carbs": str(proposal.new_carbs),
        }
        return payload
    payload.update(
        {
            "date": proposal.date.isoformat(),
            "meal_type": proposal.meal_type,
            "meal_label": _MEAL_LABELS[proposal.meal_type],
            "amount": str(proposal.amount),
        }
    )
    if proposal.product_id:
        payload["product"] = _product(proposal.product)
    else:
        payload["new_product"] = {
            "name": proposal.new_name,
            "base_unit": proposal.new_base_unit,
            "calories": str(proposal.new_calories),
            "proteins": str(proposal.new_proteins),
            "fats": str(proposal.new_fats),
            "carbs": str(proposal.new_carbs),
        }
    return payload


def search_catalog(member: Any, query: Any) -> dict[str, Any]:
    """Ищет Продукты общего Каталога по названию; ничего не меняет."""
    text = str(query or "").strip()
    if not text:
        return {"error": "Укажите название для поиска"}
    found = search_products(text)
    return {
        "products": [_product(product) for product in found[:MAX_CATALOG_RESULTS]],
        "total": len(found),
        "truncated": len(found) > MAX_CATALOG_RESULTS,
    }


def lookup_barcode(member: Any, code: Any) -> dict[str, Any]:
    """Ищет Штрихкод в Каталоге, а при промахе спрашивает Open Food Facts."""
    normalized = normalize_barcode(str(code or ""))
    if normalized is None:
        return {"error": "Штрихкод неполный или с неверной контрольной цифрой"}
    barcode = Barcode.objects.select_related("product").filter(code=normalized).first()
    if barcode is not None:
        return {"code": normalized, "product": _product(barcode.product), "off": None}
    return {"code": normalized, "product": None, "off": _off(off_lookup(normalized))}


def read_diary(member: Any, date: Any, date_to: Any = None) -> dict[str, Any]:
    """Читает Записи Дневника Члена семьи за день или диапазон до 31 дня."""
    start = _iso_date(date)
    if start is None:
        return {"error": "Дата должна быть в формате ГГГГ-ММ-ДД"}
    end = _iso_date(date_to) if date_to else start
    if end is None:
        return {"error": "Конечная дата должна быть в формате ГГГГ-ММ-ДД"}
    if end < start:
        return {"error": "Конец диапазона раньше начала"}
    if (end - start).days + 1 > MAX_DIARY_DAYS:
        return {"error": f"Диапазон не больше {MAX_DIARY_DAYS} дней"}
    entries = (
        DiaryEntry.objects.filter(member_id=member.pk, date__range=(start, end))
        .select_related("product")
        .order_by("date", "created_at")
    )
    days: dict[datetime.date, dict[str, Any]] = {}
    for entry in entries:
        day = days.setdefault(
            entry.date,
            {
                "date": entry.date.isoformat(),
                "entries": [],
                "calories": Decimal(0),
                "proteins": Decimal(0),
                "fats": Decimal(0),
                "carbs": Decimal(0),
            },
        )
        day["entries"].append(
            {
                "product": entry.product.name,
                "amount": str(entry.amount),
                "base_unit": entry.product.base_unit,
                "meal_type": entry.meal_type,
                "meal_label": entry.get_meal_type_display(),
                "calories": str(entry.calories),
                "proteins": str(entry.proteins),
                "fats": str(entry.fats),
                "carbs": str(entry.carbs),
            }
        )
        for field in ("calories", "proteins", "fats", "carbs"):
            day[field] += getattr(entry, field)
    return {
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "days": [
            {
                **day,
                "calories": str(day["calories"]),
                "proteins": str(day["proteins"]),
                "fats": str(day["fats"]),
                "carbs": str(day["carbs"]),
            }
            for day in days.values()
        ],
    }


def read_profile(member: Any) -> dict[str, Any]:
    """Читает Профиль, Суточную цель и предложение цели, если Профиль уже есть."""
    target = DailyTarget.objects.filter(member_id=member.pk).first()
    return {
        "profile": _profile(member),
        "target": _target(target),
        "suggestion": _suggestion(member),
    }


def _clarification(missing: list[str]) -> dict[str, Any]:
    questions = []
    if "date" in missing:
        questions.append("На какой день добавить Запись? Формат ГГГГ-ММ-ДД.")
    if "meal_type" in missing:
        labels = ", ".join(_MEAL_LABELS[value].lower() for value in _MEAL_VALUES)
        questions.append(f"В какой Приём пищи? Варианты: {labels}.")
    return {"needs_clarification": True, "missing": missing, "question": " ".join(questions)}


def propose_product(member: Any, new_product: Any) -> dict[str, Any]:
    """Готовит Предложение нового Продукта для Каталога, без Записи дневника."""
    proposal = create_product_proposal(member, new_product=new_product)
    return {
        "proposal": _proposal(proposal),
        "similar": [_product(product) for product in similar_products(proposal.new_name)],
    }


def propose_entry(
    member: Any,
    amount: Any = None,
    date: Any = None,
    meal_type: Any = None,
    product_id: Any = None,
    new_product: Any = None,
) -> dict[str, Any]:
    """Готовит Предложение Записи; без ясных даты и Приёма пищи его не создаёт."""
    missing = []
    if not str(date or "").strip():
        missing.append("date")
    if not str(meal_type or "").strip():
        missing.append("meal_type")
    if missing:
        return _clarification(missing)
    if amount in (None, ""):
        return {"error": "Укажите количество в граммах или миллилитрах"}
    proposal = create_proposal(
        member,
        amount=amount,
        date=date,
        meal_type=meal_type,
        product_id=product_id,
        new_product=new_product,
    )
    similar = similar_products(proposal.new_name) if proposal.product_id is None else []
    return {
        "proposal": _proposal(proposal),
        "similar": [_product(product) for product in similar],
    }


_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "search_catalog": search_catalog,
    "lookup_barcode": lookup_barcode,
    "read_diary": read_diary,
    "read_profile": read_profile,
    "propose_product": propose_product,
    "propose_entry": propose_entry,
}


def _parse_arguments(arguments: Any) -> dict[str, Any]:
    if arguments is None or arguments == "":
        return {}
    if isinstance(arguments, Mapping):
        parsed: Any = dict(arguments)
    else:
        try:
            parsed = json.loads(str(arguments))
        except ValueError as error:
            raise ToolArgumentError from error
    if not isinstance(parsed, dict):
        raise ToolArgumentError
    return parsed


def dispatch_tool(member: Any, name: str, arguments: Any = None) -> dict[str, Any]:
    """Вызывает инструмент по имени с аргументами модели; ошибки возвращает данными.

    Args:
        member: вошедший Член семьи; его личные данные только и читаются.
        name: имя инструмента из `TOOLS`.
        arguments: JSON-строка или объект с аргументами.

    Returns:
        JSON-совместимый результат либо `{"error": "..."}`; исключения модели не нужны.
    """
    if not getattr(member, "is_authenticated", False):
        return {"error": "Нужен вход в аккаунт"}
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"error": f"Неизвестный инструмент: {name}"}
    try:
        kwargs = _parse_arguments(arguments)
    except ToolArgumentError:
        return {"error": "Аргументы инструмента должны быть JSON-объектом"}
    signature = inspect.signature(handler)
    try:
        signature.bind(member, **kwargs)
    except TypeError:
        allowed = [name for name in signature.parameters if name != "member"]
        return {
            "error": (
                f"Неверные аргументы инструмента «{name}». "
                f"Допустимые: {', '.join(allowed) if allowed else '—'}."
            )
        }
    try:
        return handler(member, **kwargs)
    except ProposalError as error:
        return {"error": str(error)}


def _function(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


_NEW_PRODUCT_SCHEMA = {
    "type": "object",
    "description": "Данные нового Продукта, если его нет в Каталоге.",
    "properties": {
        "name": {"type": "string", "description": "Название Продукта"},
        "base_unit": {
            "type": "string",
            "enum": list(Product.BaseUnit.values),
            "description": "Базовая единица: g для граммов, ml для миллилитров",
        },
        "calories": {"type": "number", "description": "ккал на 100 г или мл"},
        "proteins": {"type": "number", "description": "белки на 100 г или мл"},
        "fats": {"type": "number", "description": "жиры на 100 г или мл"},
        "carbs": {"type": "number", "description": "углеводы на 100 г или мл"},
    },
    "required": ["name", "base_unit", "calories", "proteins", "fats", "carbs"],
    "additionalProperties": False,
}

TOOLS: list[dict[str, Any]] = [
    _function(
        "search_catalog",
        "Найти Продукты в общем Каталоге по названию. Каталог ничего не меняет.",
        {"query": {"type": "string", "description": "Название или его часть"}},
        ["query"],
    ),
    _function(
        "lookup_barcode",
        "Найти Продукт по Штрихкоду; если кода нет в Каталоге, вернуть данные Open Food Facts.",
        {"code": {"type": "string", "description": "Цифры Штрихкода"}},
        ["code"],
    ),
    _function(
        "read_diary",
        "Прочитать Записи Дневника Члена семьи за день или диапазон до 31 дня.",
        {
            "date": {"type": "string", "format": "date", "description": "Первый день, ГГГГ-ММ-ДД"},
            "date_to": {
                "type": "string",
                "format": "date",
                "description": "Последний день диапазона, если нужен не один день",
            },
        },
        ["date"],
    ),
    _function(
        "read_profile",
        "Прочитать Профиль, Суточную цель и предложение цели Члена семьи.",
        {},
        [],
    ),
    _function(
        "propose_product",
        ("Подготовить Предложение нового Продукта только для Каталога, без Записи дневника."),
        {"new_product": _NEW_PRODUCT_SCHEMA},
        ["new_product"],
    ),
    _function(
        "propose_entry",
        (
            "Подготовить Предложение Записи с существующим или новым Продуктом. "
            "Дата и Приём пищи обязательны: если человек их не назвал, не создавай "
            "Предложение, а спроси недостающее."
        ),
        {
            "amount": {"type": "number", "description": "Количество в г или мл"},
            "date": {"type": "string", "format": "date", "description": "День Записи, ГГГГ-ММ-ДД"},
            "meal_type": {"type": "string", "enum": _MEAL_VALUES, "description": "Приём пищи"},
            "product_id": {"type": "integer", "description": "id Продукта из Каталога"},
            "new_product": _NEW_PRODUCT_SCHEMA,
        },
        ["amount"],
    ),
]
