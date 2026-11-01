"""Жизненный цикл Предложения: подготовка, сопоставление с Каталогом, решение.

Предложение ничего не меняет до подтверждения. Подтверждение атомарно создаёт
Запись дневника и/или новый Продукт; повтор безопасен.
"""

import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from django.db import transaction
from django.utils import timezone

from assistant.models import Proposal
from core.models import DiaryEntry, Product


class ProposalError(Exception):
    """Предложение нельзя создать или решить так, как просят."""


class ProposalNotFound(ProposalError):
    """Предложения нет у этого Члена семьи."""


class ProposalDecided(ProposalError):
    """Предложение уже решено иначе, поэтому действие не применяется."""


MAX_NUTRITION = Decimal("1000000")

PRODUCT_EDITABLE_FIELDS = (
    "new_name",
    "new_base_unit",
    "new_calories",
    "new_proteins",
    "new_fats",
    "new_carbs",
)

ENTRY_EDITABLE_FIELDS = (
    "product",
    *PRODUCT_EDITABLE_FIELDS,
    "amount",
    "date",
    "meal_type",
)


def similar_products(name: str, limit: int = 5) -> list[Product]:
    """Похожие Продукты Каталога для выбора; ничего не создаёт и не меняет."""
    needle = " ".join(name.lower().split())
    if not needle:
        return []
    matches: list[Product] = []
    for product in Product.objects.all():
        candidate = " ".join(product.name.lower().split())
        if needle in candidate or candidate in needle:
            matches.append(product)
            if len(matches) == limit:
                break
    return matches


def _decimal(value: Any, *, field: str, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ProposalError(f"{field}: ожидается число")
    try:
        number = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError) as error:
        raise ProposalError(f"{field}: ожидается число") from error
    if not number.is_finite() or number < 0:
        raise ProposalError(f"{field}: ожидается неотрицательное число")
    if number >= MAX_NUTRITION:
        raise ProposalError(f"{field}: значение слишком большое")
    # Поля хранят один знак после запятой; проверяем уже округлённое значение.
    try:
        number = number.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    except InvalidOperation as error:
        raise ProposalError(f"{field}: значение слишком большое") from error
    if positive and number == 0:
        raise ProposalError(f"{field}: значение должно быть больше нуля")
    if number >= MAX_NUTRITION:
        raise ProposalError(f"{field}: значение слишком большое")
    return number


def _amount(value: Any) -> Decimal:
    number = _decimal(value, field="Количество", positive=True)
    if number > DiaryEntry.MAX_AMOUNT:
        raise ProposalError(f"Количество не может быть больше {DiaryEntry.MAX_AMOUNT:g}")
    return number


def _stored(value: Decimal | None) -> Decimal:
    """Поле нового Продукта из уже сохранённого Предложения; без него решать нельзя."""
    if value is None:
        raise ProposalError("У нового Продукта не хватает КБЖУ")
    return value


def _date(value: Any) -> datetime.date:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value).strip())
    except ValueError as error:
        raise ProposalError("Дата должна быть в формате ГГГГ-ММ-ДД") from error


def _meal(value: Any) -> str:
    meal = str(value or "").strip()
    if meal not in DiaryEntry.MealType.values:
        labels = ", ".join(label for _value, label in DiaryEntry.MealType.choices)
        raise ProposalError(f"Приём пищи должен быть одним из: {labels}")
    return meal


def _new_product(values: Any) -> dict[str, Any]:
    if not isinstance(values, dict):
        raise ProposalError("Новый Продукт задаётся объектом с названием, единицей и КБЖУ")
    name = str(values.get("name") or "").strip()
    if not name:
        raise ProposalError("У нового Продукта должно быть название")
    max_length = Product._meta.get_field("name").max_length
    if max_length is not None and len(name) > max_length:
        raise ProposalError("Название нового Продукта слишком длинное")
    base_unit = str(values.get("base_unit") or "").strip()
    if base_unit not in Product.BaseUnit.values:
        raise ProposalError("Базовая единица нового Продукта — «g» или «ml»")
    nutrition = {
        field: _decimal(values.get(field), field=label)
        for field, label in (
            ("calories", "Калории"),
            ("proteins", "Белки"),
            ("fats", "Жиры"),
            ("carbs", "Углеводы"),
        )
    }
    return {
        "new_name": name,
        "new_base_unit": base_unit,
        **{f"new_{field}": value for field, value in nutrition.items()},
    }


def _product_from_proposal(proposal: Proposal, member: Any) -> Product:
    """Создаёт новый Продукт из полей Предложения; автор — подтвердивший."""
    return Product.objects.create(
        name=proposal.new_name,
        base_unit=proposal.new_base_unit,
        calories=_stored(proposal.new_calories),
        proteins=_stored(proposal.new_proteins),
        fats=_stored(proposal.new_fats),
        carbs=_stored(proposal.new_carbs),
        author=member,
    )


def create_proposal(
    member: Any,
    *,
    amount: Any,
    date: Any,
    meal_type: Any,
    product_id: Any = None,
    new_product: Any = None,
) -> Proposal:
    """Создаёт Предложение в состоянии «ожидает решения»; Каталог не меняет.

    Args:
        member: Член семьи, которому принадлежит Предложение.
        amount: количество в базовых единицах Продукта.
        date: день Записи.
        meal_type: значение `DiaryEntry.MealType`.
        product_id: pk существующего Продукта Каталога.
        new_product: поля нового Продукта вместо `product_id`.

    Raises:
        ProposalError: неполные или некорректные данные либо оба/ни один источник Продукта.
    """
    if product_id is not None and new_product is not None:
        raise ProposalError("Укажите либо Продукт Каталога, либо данные нового Продукта")
    if product_id is None and new_product is None:
        raise ProposalError("Укажите Продукт Каталога или данные нового Продукта")
    product = None
    new_fields: dict[str, Any] = {}
    if product_id is not None:
        try:
            product = Product.objects.filter(pk=product_id).first()
        except (TypeError, ValueError) as error:
            raise ProposalError("Продукт не найден в Каталоге") from error
        if product is None:
            raise ProposalError("Продукт не найден в Каталоге")
    else:
        new_fields = _new_product(new_product)
    return Proposal.objects.create(
        member=member,
        kind=Proposal.Kind.ENTRY,
        date=_date(date),
        meal_type=_meal(meal_type),
        amount=_amount(amount),
        product=product,
        **new_fields,
    )


def create_product_proposal(member: Any, *, new_product: Any) -> Proposal:
    """Создаёт Предложение нового Продукта без Записи дневника; Каталог не меняет."""
    return Proposal.objects.create(
        member=member,
        kind=Proposal.Kind.PRODUCT,
        **_new_product(new_product),
    )


def update_pending_proposal(member: Any, proposal_id: int, values: dict[str, Any]) -> Proposal:
    """Применяет правку карточки, только пока Предложение ожидает решения.

    Строка блокируется на время записи, поэтому одновременное подтверждение
    не перезаписывается обратно в «ожидает».

    Raises:
        ProposalNotFound: Предложения нет у этого Члена семьи или оно уже решено.
    """
    with transaction.atomic():
        proposal = (
            Proposal.objects.select_for_update()
            .filter(pk=proposal_id, member=member, status=Proposal.Status.PENDING)
            .first()
        )
        if proposal is None:
            raise ProposalNotFound("Предложение не найдено или уже решено")
        fields = (
            PRODUCT_EDITABLE_FIELDS
            if proposal.kind == Proposal.Kind.PRODUCT
            else ENTRY_EDITABLE_FIELDS
        )
        for field in fields:
            setattr(proposal, field, values[field])
        proposal.save(update_fields=list(fields))
        return proposal


def confirm_proposal(member: Any, proposal_id: int) -> Proposal:
    """Подтверждает Предложение один раз: создаёт Запись и/или новый Продукт атомарно.

    Raises:
        ProposalNotFound: Предложения нет у этого Члена семьи.
        ProposalDecided: Предложение уже отклонено — данные не создаются.
        ProposalError: у Предложения не хватает полей для создания данных.
    """
    with transaction.atomic():
        proposal = (
            Proposal.objects.select_for_update().filter(pk=proposal_id, member=member).first()
        )
        if proposal is None:
            raise ProposalNotFound("Предложение не найдено")
        if proposal.status == Proposal.Status.CONFIRMED:
            return proposal
        if proposal.status == Proposal.Status.REJECTED:
            raise ProposalDecided("Предложение уже отклонено")

        if proposal.kind == Proposal.Kind.PRODUCT:
            created = _product_from_proposal(proposal, member)
            proposal.product = created
            proposal.status = Proposal.Status.CONFIRMED
            proposal.decided_at = timezone.now()
            proposal.save(update_fields=["product", "status", "decided_at"])
            return proposal

        product = proposal.product
        if product is None:
            product = _product_from_proposal(proposal, member)
        if proposal.date is None or proposal.amount is None or not proposal.meal_type:
            raise ProposalError("У Предложения не хватает даты, количества или Приёма пищи")
        entry = DiaryEntry.objects.create(
            member=member,
            date=proposal.date,
            meal_type=proposal.meal_type,
            product=product,
            amount=proposal.amount,
        )
        proposal.product = product
        proposal.entry = entry
        proposal.status = Proposal.Status.CONFIRMED
        proposal.decided_at = timezone.now()
        proposal.save(update_fields=["product", "entry", "status", "decided_at"])
        return proposal


def reject_proposal(member: Any, proposal_id: int) -> Proposal:
    """Отклоняет ожидающее Предложение; данные не создаёт, повтор безопасен.

    Raises:
        ProposalNotFound: Предложения нет у этого Члена семьи.
        ProposalDecided: Предложение уже подтверждено — отменять его так нельзя.
    """
    with transaction.atomic():
        proposal = (
            Proposal.objects.select_for_update().filter(pk=proposal_id, member=member).first()
        )
        if proposal is None:
            raise ProposalNotFound("Предложение не найдено")
        if proposal.status == Proposal.Status.CONFIRMED:
            raise ProposalDecided("Предложение уже подтверждено")
        if proposal.status == Proposal.Status.PENDING:
            proposal.status = Proposal.Status.REJECTED
            proposal.decided_at = timezone.now()
            proposal.save(update_fields=["status", "decided_at"])
        return proposal
