"""Очередь фоновых задач django.tasks и её обслуживание."""

import logging
from datetime import timedelta
from functools import partial
from typing import Any

from django.conf import settings
from django.core.management import call_command
from django.db import transaction
from django.tasks import Task, TaskResultStatus
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

logger = logging.getLogger(__name__)


def enqueue_on_commit(task: Task, /, *args: Any, **kwargs: Any) -> None:
    """Ставит задачу в очередь после коммита текущей транзакции.

    Воркер не увидит задачу, поставленную до коммита, поэтому ставить её
    раньше нельзя.

    Args:
        task: задача, созданная декоратором `django.tasks.task`.
        *args: позиционные аргументы задачи; проходят JSON round-trip.
        **kwargs: именованные аргументы задачи; проходят JSON round-trip.
    """
    transaction.on_commit(partial(task.enqueue, *args, **kwargs))


def recover_interrupted() -> int:
    """Возвращает в очередь задачи, чей воркер умер посреди выполнения.

    Returns:
        Сколько задач вернулось в очередь.
    """
    cutoff = timezone.now() - timedelta(minutes=settings.TASKS_STUCK_AFTER_MINUTES)
    return (
        DBTaskResult.objects.running()
        .filter(started_at__lt=cutoff)
        .update(
            status=TaskResultStatus.READY,
            started_at=None,
        )
    )


def prune_results() -> None:
    """Удаляет завершённые результаты задач старше срока хранения."""
    call_command(
        "prune_db_task_results",
        queue_name="*",
        min_age_days=settings.TASKS_RESULT_RETENTION_DAYS,
        verbosity=0,
    )


def run_tick() -> int:
    """Один идемпотентный проход обслуживания очереди.

    Планировщик вызывает тик периодически; состояние читается из БД, поэтому
    пропущенный или повторный запуск ничего не ломает.

    Returns:
        Сколько зависших задач вернулось в очередь.
    """
    recovered = recover_interrupted()
    if recovered:
        logger.warning("Recovered %d interrupted background task(s)", recovered)
    prune_results()
    return recovered
