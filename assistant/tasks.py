"""Фоновые задачи Ассистента."""

from django.tasks import task

from assistant.chat import execute_turn


@task
def execute_turn_task(turn_id: int) -> None:
    """Выполняет ход в воркере очереди; повторная доставка задачи безопасна."""
    execute_turn(turn_id)
