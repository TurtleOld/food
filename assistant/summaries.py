"""Сводки: детерминированные цифры дня и недели, необязательный совет модели, доставка.

Цифры считаются по Снапшотам Записей и не зависят от модели. Модель пишет только
короткий совет; при исчерпанном бюджете или ошибке провайдера Сводка всё равно
отправляется. Отправленный текст неизменяем, а уникальность по человеку, виду и
началу местного периода исключает повторную отправку при повторном тике.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from assistant.budget import BudgetUnavailable
from assistant.gateway import LLMGatewayError, complete
from assistant.models import Conversation, Message, Summary, SummarySettings, Turn
from core.jobs import enqueue_on_commit
from core.models import DailyTarget, DiaryEntry, WeightMeasurement
from core.push import notification_payload

logger = logging.getLogger(__name__)

MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
ADVICE_PROMPT = (
    "Ты — Ассистент семейного трекера КБЖУ. Напиши один короткий (до 300 символов) "
    "бытовой комментарий к сводке: поддержи, отметь баланс или предложи простой "
    "следующий шаг. Не ставь диагнозы, не назначай лечение, лекарства, добавки или "
    "медицинские процедуры. Не выдумывай цифры, которых нет в сводке."
)
PUSH_TITLE = {
    Summary.Kind.EVENING: "Вечерняя сводка",
    Summary.Kind.WEEKLY: "Недельная сводка",
}


@dataclass(frozen=True)
class Totals:
    """Сумма КБЖУ за период."""

    calories: Decimal
    proteins: Decimal
    fats: Decimal
    carbs: Decimal


def _num(value: Decimal) -> str:
    """Число для текста: целые без дробной части, дробные — с запятой."""
    rounded = value.quantize(Decimal("0.1"))
    if rounded == rounded.to_integral_value():
        return f"{int(rounded)}"
    return f"{rounded}".replace(".", ",")


def _day_label(day: datetime.date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]}"


def _period_label(kind: str, period_start: datetime.date) -> str:
    if kind == Summary.Kind.WEEKLY:
        end = period_start + datetime.timedelta(days=6)
        return f"{_day_label(period_start)} — {_day_label(end)}"
    return _day_label(period_start)


def _totals(member_id: int, start: datetime.date, end: datetime.date) -> Totals:
    """Суммирует съеденное за включительный диапазон дней по Снапшотам Записей."""
    entries = DiaryEntry.objects.filter(
        member_id=member_id,
        date__gte=start,
        date__lte=end,
    ).only(
        "amount",
        "calories_snapshot",
        "proteins_snapshot",
        "fats_snapshot",
        "carbs_snapshot",
    )
    total = Totals(Decimal(0), Decimal(0), Decimal(0), Decimal(0))
    for entry in entries:
        total = Totals(
            calories=total.calories + entry.calories,
            proteins=total.proteins + entry.proteins,
            fats=total.fats + entry.fats,
            carbs=total.carbs + entry.carbs,
        )
    return total


def _has_entries(member_id: int, start: datetime.date, end: datetime.date) -> bool:
    return DiaryEntry.objects.filter(
        member_id=member_id,
        date__gte=start,
        date__lte=end,
    ).exists()


def _target(member_id: int) -> DailyTarget | None:
    return DailyTarget.objects.filter(member_id=member_id).first()


def _deviation(total: Decimal, target: Decimal, unit: str) -> str:
    delta = total - target
    if delta == 0:
        return f"ровно по цели {_num(target)} {unit}"
    direction = "больше" if delta > 0 else "меньше"
    return f"на {_num(abs(delta))} {unit} {direction} цели {_num(target)} {unit}"


def _macro_line(label: str, total: Decimal, target: Decimal | None) -> str:
    if target is None:
        return f"{label}: {_num(total)} г."
    return f"{label}: {_num(total)} из {_num(target)} г."


def evening_text(member_id: int, day: datetime.date) -> str:
    """Детерминированный текст вечерней Сводки за день без совета модели."""
    header = f"Вечерняя сводка за {_day_label(day)}."
    if not _has_entries(member_id, day, day):
        return f"{header}\n\nСегодня в Дневнике нет записей. Добавьте съеденное, чтобы видеть КБЖУ."

    totals = _totals(member_id, day, day)
    target = _target(member_id)
    lines = [header, ""]
    if target is None:
        lines.append(f"Калории: {_num(totals.calories)} ккал. Суточная цель не задана.")
        lines.append(_macro_line("Белки", totals.proteins, None))
        lines.append(_macro_line("Жиры", totals.fats, None))
        lines.append(_macro_line("Углеводы", totals.carbs, None))
    else:
        lines.append(f"Калории: {_deviation(totals.calories, target.calories, 'ккал')}.")
        lines.append(_macro_line("Белки", totals.proteins, target.proteins))
        lines.append(_macro_line("Жиры", totals.fats, target.fats))
        lines.append(_macro_line("Углеводы", totals.carbs, target.carbs))

    yesterday = day - datetime.timedelta(days=1)
    if _has_entries(member_id, yesterday, yesterday):
        yesterday_totals = _totals(member_id, yesterday, yesterday)
        lines.append(f"Вчера: {_num(yesterday_totals.calories)} ккал.")
    else:
        lines.append("Вчера записей не было.")
    return "\n".join(lines)


def _weight_trajectory(member_id: int, until: datetime.date) -> str | None:
    """Последний Замер к концу периода и разница с предыдущим Замером."""
    measurements = list(
        WeightMeasurement.objects.filter(member_id=member_id, date__lte=until).order_by(
            "-date", "-id"
        )[:2]
    )
    if not measurements:
        return None
    latest = measurements[0]
    line = f"Вес: {_num(latest.weight_kg)} кг ({_day_label(latest.date)})."
    if len(measurements) == 2:
        delta = latest.weight_kg - measurements[1].weight_kg
        if delta == 0:
            line += " Без изменений к прошлому Замеру."
        else:
            sign = "+" if delta > 0 else "−"
            line += f" {sign}{_num(abs(delta))} кг к прошлому Замеру."
    return line


def weekly_text(member_id: int, period_start: datetime.date) -> str:
    """Детерминированный текст недельной Сводки за семь дней без совета модели."""
    end = period_start + datetime.timedelta(days=6)
    header = f"Недельная сводка: {_period_label(Summary.Kind.WEEKLY, period_start)}."
    lines = [header, ""]
    if not _has_entries(member_id, period_start, end):
        lines.append("За неделю в Дневнике нет записей. Добавьте съеденное, чтобы видеть КБЖУ.")
    else:
        totals = _totals(member_id, period_start, end)
        average = totals.calories / 7
        lines.append(
            f"За 7 дней: {_num(totals.calories)} ккал, в среднем {_num(average)} ккал в день."
        )
        lines.append(
            f"Белки: {_num(totals.proteins)} г, жиры: {_num(totals.fats)} г, "
            f"углеводы: {_num(totals.carbs)} г."
        )
        target = _target(member_id)
        if target is not None:
            lines.append(f"Цель: {_num(target.calories)} ккал в день.")
            lines.append(f"Среднее за день — {_deviation(average, target.calories, 'ккал')}.")

    trajectory = _weight_trajectory(member_id, end)
    if trajectory is not None:
        lines.append(trajectory)
    return "\n".join(lines)


def _advice(text: str) -> str:
    """Короткий совет модели; без бюджета или провайдера остаётся пустым."""
    try:
        result = complete(
            [
                {"role": "system", "content": ADVICE_PROMPT},
                {"role": "user", "content": text},
            ]
        )
    except (BudgetUnavailable, LLMGatewayError):
        logger.info("Summary advice skipped: model is unavailable")
        return ""
    return result.text.strip()


def build_text(
    member_id: int,
    kind: str,
    period_start: datetime.date,
    *,
    with_advice: bool = True,
) -> str:
    """Собирает текст Сводки: детерминированные цифры и, если получится, совет."""
    if kind == Summary.Kind.WEEKLY:
        text = weekly_text(member_id, period_start)
        has_entries = _has_entries(
            member_id, period_start, period_start + datetime.timedelta(days=6)
        )
    else:
        text = evening_text(member_id, period_start)
        has_entries = _has_entries(member_id, period_start, period_start)
    # Пустой день — это напоминание, а не повод тратить бюджет на совет.
    if with_advice and has_entries:
        advice = _advice(text)
        if advice:
            text = f"{text}\n\nСовет: {advice}"
    return text


def _post_message(member: Any, summary: Summary, now: datetime.datetime) -> None:
    """Кладёт Сводку в личную ленту Ассистента отдельным завершённым ходом."""
    conversation = Conversation.for_member(member)
    turn = Turn.objects.create(
        conversation=conversation,
        status=Turn.Status.SUCCEEDED,
        request_dedupe_key=f"summary:{summary.kind}:{summary.period_start.isoformat()}",
        started_at=now,
        finished_at=now,
    )
    Message.objects.create(
        conversation=conversation,
        turn=turn,
        role=Message.Role.ASSISTANT,
        text=summary.text,
    )


def generate_summary(
    member_id: int,
    kind: str,
    period_start: datetime.date,
    *,
    now: datetime.datetime | None = None,
) -> Summary | None:
    """Создаёт и доставляет Сводку ровно один раз за период.

    Args:
        member_id: Член семьи, чей личный Дневник читается.
        kind: вид Сводки — вечерняя или недельная.
        period_start: начало местного периода; для недели — понедельник.
        now: момент отправки, по умолчанию текущий.

    Returns:
        Созданная Сводка или None, если за этот период она уже отправлена.
    """
    moment = now or timezone.now()
    if kind not in Summary.Kind.values:
        raise ValueError(f"Неизвестный вид Сводки: {kind}")
    member = get_user_model().objects.filter(pk=member_id, is_active=True).first()
    if member is None:
        return None
    if Summary.objects.filter(member_id=member_id, kind=kind, period_start=period_start).exists():
        return None

    text = build_text(member_id, kind, period_start)
    try:
        with transaction.atomic():
            summary = Summary.objects.create(
                member_id=member_id,
                kind=kind,
                period_start=period_start,
                text=text,
            )
            _post_message(member, summary, moment)
            enqueue_on_commit(
                _push_task(),
                member_id,
                notification_payload(
                    PUSH_TITLE[Summary.Kind(kind)],
                    _push_body(summary),
                    tag=f"summary-{kind}",
                ),
            )
    except IntegrityError:
        # Параллельный тик успел первым: его Сводка и его сообщение уже в базе.
        return None
    return summary


def _push_body(summary: Summary) -> str:
    """Тело уведомления: первые строки Сводки без заголовка."""
    lines = [line for line in summary.text.splitlines() if line.strip()]
    return "\n".join(lines[1:]) if len(lines) > 1 else summary.text


def _push_task() -> Any:
    """Задача доставки Push; импорт отложен, чтобы не было цикла модулей."""
    from assistant.tasks import push_summary_task

    return push_summary_task


def due_summaries(
    now: datetime.datetime | None = None,
) -> list[tuple[int, str, datetime.date]]:
    """Возвращает ещё не отправленные Сводки, время которых наступило.

    Границы периодов — локальный календарь приложения, а не UTC: вечерняя Сводка
    относится к текущему местному дню, недельная — к неделе с понедельника по
    воскресенье и отправляется только в воскресенье.

    Args:
        now: момент проверки, по умолчанию текущий.

    Returns:
        Пары «человек, вид, начало периода» для постановки задач.
    """
    local = timezone.localtime(now or timezone.now())
    today = local.date()
    local_time = local.time().replace(second=0, microsecond=0)
    week_start = today - datetime.timedelta(days=today.weekday())
    due: list[tuple[int, str, datetime.date]] = []
    members = get_user_model().objects.filter(is_active=True)
    for member in members:
        summary_settings = SummarySettings.for_member(member)
        candidates: list[tuple[str, datetime.date, bool]] = [
            (
                Summary.Kind.EVENING,
                today,
                summary_settings.evening_enabled and local_time >= summary_settings.evening_time,
            ),
            (
                Summary.Kind.WEEKLY,
                week_start,
                summary_settings.weekly_enabled
                and today.weekday() == 6
                and local_time >= summary_settings.weekly_time,
            ),
        ]
        for kind, period_start, is_due in candidates:
            if not is_due:
                continue
            sent = Summary.objects.filter(
                member_id=member.pk,
                kind=kind,
                period_start=period_start,
            ).exists()
            if not sent:
                due.append((member.pk, kind, period_start))
    return due


def schedule_due(now: datetime.datetime | None = None) -> int:
    """Ставит задачи для наступивших Сводок; повторный тик не создаёт дублей.

    Returns:
        Сколько задач поставлено в очередь.
    """
    from assistant.tasks import generate_summary_task

    due = due_summaries(now)
    for member_id, kind, period_start in due:
        generate_summary_task.enqueue(member_id, kind, period_start.isoformat())
    return len(due)
