"""Фоновые задачи Ассистента."""

import datetime
import logging
from typing import Any

from django.tasks import task

from assistant.chat import execute_turn
from assistant.summaries import generate_summary
from core.push import PushNotConfigured, PushPayloadTooLarge, send_push

logger = logging.getLogger(__name__)


@task
def execute_turn_task(turn_id: int) -> None:
    """Выполняет ход в воркере очереди; повторная доставка задачи безопасна."""
    execute_turn(turn_id)


@task
def generate_summary_task(member_id: int, kind: str, period_start: str) -> None:
    """Создаёт Сводку за период; повторная доставка видит готовую строку и молчит."""
    generate_summary(member_id, kind, datetime.date.fromisoformat(period_start))


@task
def push_summary_task(member_id: int, payload: dict[str, Any]) -> None:
    """Доставляет уведомление о Сводке; ошибка Push не касается уже сохранённого текста."""
    try:
        send_push(member_id, payload)
    except PushNotConfigured:
        logger.info("Summary push skipped: VAPID is not configured")
    except PushPayloadTooLarge:
        logger.warning("Summary push skipped: payload is too large")
