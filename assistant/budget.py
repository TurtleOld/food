"""Общий семейный бюджет: резерв до запроса, факт после ответа, месяц по местному времени."""

import datetime
import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from assistant.models import BudgetReservation, LLMSettings, UsageRecord

MILLION = Decimal(1_000_000)
COST_QUANTUM = Decimal("0.000001")
# Запрос ограничен таймаутом настройки (не больше 120 с), поэтому более старый резерв
# остался после падения процесса и больше не защищает никакого запроса.
STALE_RESERVATION = datetime.timedelta(minutes=5)
NOT_CONFIGURED = (
    "Ассистент не настроен: администратор должен указать провайдера, тарифы и месячный бюджет."
)
BUDGET_EXHAUSTED = "Месячный бюджет ассистента исчерпан."


class BudgetUnavailable(Exception):
    """Запрос отклонён до сети: нет настроек, исчерпан бюджет или вход слишком велик."""


@dataclass(frozen=True)
class BudgetSummary:
    """Расход, резервы и остаток общего бюджета за текущий месяц."""

    budget: Decimal
    spent: Decimal
    reserved: Decimal
    remaining: Decimal
    configured: bool


def month_bounds(
    moment: datetime.datetime | None = None,
) -> tuple[datetime.datetime, datetime.datetime]:
    """Границы календарного месяца в часовом поясе приложения."""
    local = timezone.localtime(moment or timezone.now())
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def _cost(
    *,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    input_price_rub: Decimal,
    cached_price_rub: Decimal,
    output_price_rub: Decimal,
) -> Decimal:
    """Стоимость в рублях: кэшированные входные токены идут по отдельному тарифу."""
    fresh_input = input_tokens - cached_input_tokens
    rubles = (
        fresh_input * input_price_rub
        + cached_input_tokens * cached_price_rub
        + output_tokens * output_price_rub
    )
    return (rubles / MILLION).quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)


def _tariffs(settings: LLMSettings) -> tuple[Decimal, Decimal, Decimal]:
    assert settings.input_price_rub is not None
    assert settings.cached_price_rub is not None
    assert settings.output_price_rub is not None
    return settings.input_price_rub, settings.cached_price_rub, settings.output_price_rub


def estimate_max_cost(settings: LLMSettings) -> Decimal:
    """Верхняя оценка запроса по лимитам токенов, чтобы зарезервировать её заранее."""
    input_price, cached_price, output_price = _tariffs(settings)
    return _cost(
        input_tokens=settings.max_input_tokens,
        cached_input_tokens=0,
        output_tokens=settings.max_output_tokens,
        input_price_rub=input_price,
        cached_price_rub=cached_price,
        output_price_rub=output_price,
    )


def month_summary(
    moment: datetime.datetime | None = None, settings: LLMSettings | None = None
) -> BudgetSummary:
    """Считает факт и активные резервы текущего месяца; прошлые месяцы не входят."""
    settings = settings or LLMSettings.current()
    start, end = month_bounds(moment)
    spent = UsageRecord.objects.filter(created_at__gte=start, created_at__lt=end).aggregate(
        total=Sum("cost_rub")
    )["total"] or Decimal("0")
    reserved = BudgetReservation.objects.filter(
        status=BudgetReservation.Status.PENDING,
        created_at__gte=start,
        created_at__lt=end,
    ).aggregate(total=Sum("amount_rub"))["total"] or Decimal("0")
    budget = settings.monthly_budget_rub
    return BudgetSummary(
        budget=budget,
        spent=spent,
        reserved=reserved,
        remaining=budget - spent - reserved,
        configured=settings.is_configured,
    )


def _lock_settings() -> LLMSettings | None:
    """Сериализует изменения бюджета одной строкой настроек: так резервы не гоняются."""
    return LLMSettings.objects.select_for_update().order_by("pk").first()


def reserve(request_id: str | None = None) -> BudgetReservation:
    """Проверяет остаток и резервирует верхнюю оценку запроса в одной транзакции."""
    with transaction.atomic():
        settings = _lock_settings()
        if settings is None or not settings.is_configured:
            raise BudgetUnavailable(NOT_CONFIGURED)
        now = timezone.now()
        BudgetReservation.objects.filter(
            status=BudgetReservation.Status.PENDING,
            created_at__lt=now - STALE_RESERVATION,
        ).update(status=BudgetReservation.Status.RELEASED, closed_at=now)
        estimate = estimate_max_cost(settings)
        if estimate > month_summary(settings=settings).remaining:
            raise BudgetUnavailable(BUDGET_EXHAUSTED)
        input_price, cached_price, output_price = _tariffs(settings)
        return BudgetReservation.objects.create(
            request_id=request_id or uuid.uuid4().hex,
            amount_rub=estimate,
            model=settings.model,
            input_price_rub=input_price,
            cached_price_rub=cached_price,
            output_price_rub=output_price,
        )


def settle(
    reservation: BudgetReservation,
    *,
    input_tokens: int,
    cached_input_tokens: int = 0,
    output_tokens: int,
) -> UsageRecord:
    """Записывает фактический usage по тарифам резерва и закрывает его."""
    if not 0 <= cached_input_tokens <= input_tokens or output_tokens < 0:
        raise ValueError("Некорректный usage провайдера")
    with transaction.atomic():
        _lock_settings()
        locked = BudgetReservation.objects.select_for_update().get(pk=reservation.pk)
        if locked.status != BudgetReservation.Status.PENDING:
            raise ValueError("Резерв уже закрыт")
        usage = UsageRecord.objects.create(
            reservation=locked,
            model=locked.model,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            input_price_rub=locked.input_price_rub,
            cached_price_rub=locked.cached_price_rub,
            output_price_rub=locked.output_price_rub,
            cost_rub=_cost(
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                output_tokens=output_tokens,
                input_price_rub=locked.input_price_rub,
                cached_price_rub=locked.cached_price_rub,
                output_price_rub=locked.output_price_rub,
            ),
        )
        locked.status = BudgetReservation.Status.SETTLED
        locked.closed_at = timezone.now()
        locked.save(update_fields=["status", "closed_at"])
    reservation.refresh_from_db()
    return usage


def release(reservation: BudgetReservation) -> None:
    """Возвращает зарезервированные деньги после ошибки; повторный вызов безопасен."""
    with transaction.atomic():
        _lock_settings()
        locked = BudgetReservation.objects.select_for_update().get(pk=reservation.pk)
        if locked.status == BudgetReservation.Status.PENDING:
            locked.status = BudgetReservation.Status.RELEASED
            locked.closed_at = timezone.now()
            locked.save(update_fields=["status", "closed_at"])
    reservation.refresh_from_db()
