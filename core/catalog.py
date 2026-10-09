"""Каталог: подсчёт использования Продукта и снимок для «Вернуть» при его удалении."""

from decimal import Decimal

from core.models import Product
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
        },
    )


@restorer("product_deleted")
def _restore_deleted_product(member: Member, payload: dict[str, str]) -> Restored:
    # Тот же pk нужен, чтобы вернуть ссылки (например, открытые вкладки) на прежний адрес.
    if Product.objects.filter(pk=payload["pk"]).exists():
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
    return Restored()
