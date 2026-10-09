"""Каталог: подсчёт использования Продукта и снимок для «Вернуть» при его удалении."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.db.models import Q

from core.barcodes import normalize_barcode
from core.diary import search_products
from core.models import Barcode, Product
from core.off import OffLookup, lookup
from core.undo import CannotUndo, Member, Restored, make_token, restorer


def entries_count(product: Product) -> int:
    """Число записей дневника, которые ссылаются на Продукт."""
    return product.diary_entries.count()


def deletion_token(product: Product, member: Member) -> str:
    """Токен «Вернуть» для удаляемого Продукта: хранит все поля, включая прежний pk."""
    return make_token(
        member,
        "product_deleted",
        {
            "pk": product.pk,
            "name": product.name,
            "base_unit": product.base_unit,
            "calories": str(product.calories),
            "proteins": str(product.proteins),
            "fats": str(product.fats),
            "carbs": str(product.carbs),
            "author": product.author_id,
            "barcodes": list(product.barcodes.values_list("code", flat=True)),
        },
    )


@restorer("product_deleted")
def _restore_deleted_product(member: Member, payload: dict[str, Any]) -> Restored:
    # Тот же pk нужен, чтобы вернуть ссылки (например, открытые вкладки) на прежний адрес.
    if Product.objects.filter(pk=payload["pk"]).exists():
        raise CannotUndo
    if not get_user_model().objects.filter(pk=payload["author"]).exists():
        raise CannotUndo
    Product.objects.create(
        pk=payload["pk"],
        name=payload["name"],
        base_unit=payload["base_unit"],
        calories=Decimal(payload["calories"]),
        proteins=Decimal(payload["proteins"]),
        fats=Decimal(payload["fats"]),
        carbs=Decimal(payload["carbs"]),
        author_id=payload["author"],
    )
    # Код, который за это время занял другой Продукт, не отбираем.
    taken = set(Barcode.objects.filter(code__in=payload["barcodes"]).values_list("code", flat=True))
    Barcode.objects.bulk_create(
        Barcode(code=code, product_id=payload["pk"])
        for code in payload["barcodes"]
        if code not in taken
    )
    return Restored()


def unbind_token(barcode: Barcode, member: Member) -> str:
    """Токен «Вернуть» для отвязанного Штрихкода."""
    return make_token(
        member, "barcode_unbound", {"code": barcode.code, "product": barcode.product_id}
    )


@restorer("barcode_unbound")
def _restore_unbound_barcode(member: Member, payload: dict[str, Any]) -> Restored:
    if Barcode.objects.filter(code=payload["code"]).exists():
        raise CannotUndo
    if not Product.objects.filter(pk=payload["product"]).exists():
        raise CannotUndo
    Barcode.objects.create(code=payload["code"], product_id=payload["product"])
    return Restored(product_id=payload["product"])


def search_catalog(query: str) -> list[Product]:
    """Продукты, у которых в названии есть `query` или чей код начинается с введённых цифр.

    UPC-A хранится как EAN-13 с ведущим нулём, поэтому префикс ищется и с нулём.
    """
    found = {product.pk: product for product in search_products(query)}
    digits = "".join(query.split())
    if digits.isascii() and digits.isdigit():
        prefixes = {digits, "0" + digits} if len(digits) <= 12 else {digits}
        matching = Q()
        for prefix in prefixes:
            matching |= Q(barcodes__code__startswith=prefix)
        for product in Product.objects.filter(matching).distinct():
            found[product.pk] = product
    return sorted(found.values(), key=lambda product: product.name)


CODE_QUERY_MIN_DIGITS = 6


@dataclass(frozen=True)
class CodeSearch:
    """Исход поиска по цифрам Штрихкода в шторке записи.

    `code` задан только у полного валидного кода; `product` — Продукт, которому он принадлежит,
    а `off` — ответ Open Food Facts, если кода нет в Каталоге.
    У неполного кода `note` объясняет, что идёт поиск по префиксу, и `products` — его совпадения.
    """

    code: str | None
    product: Product | None
    note: str
    products: list[Product]
    off: OffLookup | None = None


def search_by_code(query: str) -> CodeSearch | None:
    """Распознаёт запрос из 6+ цифр как Штрихкод; для обычного запроса возвращает `None`."""
    digits = "".join(query.split())
    if not (digits.isascii() and digits.isdigit()) or len(digits) < CODE_QUERY_MIN_DIGITS:
        return None
    code = normalize_barcode(digits)
    if code is None:
        return CodeSearch(
            code=None,
            product=None,
            note="Код неполный или неверная контрольная цифра — ищем по началу кода в каталоге",
            products=search_catalog(digits),
        )
    barcode = Barcode.objects.select_related("product").filter(code=code).first()
    if barcode:
        return CodeSearch(code=code, product=barcode.product, note="", products=[])
    return CodeSearch(code=code, product=None, note="", products=[], off=lookup(code))
