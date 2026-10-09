"""Подписанные снимки для «Вернуть»: общий механизм, не завязанный на запись дневника.

Действие, которое можно отменить, кладёт в тост токен со снимком состояния до него.
Каждый вид снимка (`kind`) регистрирует свою функцию восстановления через `restorer`.
"""

import datetime
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.contrib import messages
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.core import signing
from django.http import HttpRequest

UNDO_MAX_AGE = 300
_SALT = "core.undo"

Member = AbstractBaseUser | AnonymousUser


class CannotUndo(Exception):
    """Снимок нельзя применить: состояние уже изменилось."""


@dataclass(frozen=True)
class Restored:
    """Итог восстановления: день, чью ленту надо перерисовать, если он есть."""

    date: datetime.date | None = None


Restorer = Callable[[Member, dict[str, Any]], Restored]
_restorers: dict[str, Restorer] = {}


def restorer(kind: str) -> Callable[[Restorer], Restorer]:
    """Регистрирует функцию восстановления снимков вида `kind`."""

    def register(function: Restorer) -> Restorer:
        _restorers[kind] = function
        return function

    return register


def make_token(member: Member, kind: str, payload: dict[str, Any]) -> str:
    """Подписывает снимок `payload` вида `kind`, привязывая его к участнику."""
    return signing.dumps({"m": member.pk, "k": kind, "p": payload}, salt=_SALT, compress=True)


def restore(member: Member, token: str) -> Restored | None:
    """Применяет снимок из токена; `None`, если токен просрочен, подделан, чужой или не применим."""
    try:
        data = signing.loads(token, salt=_SALT, max_age=UNDO_MAX_AGE)
        if data["m"] != member.pk:
            return None
        return _restorers[data["k"]](member, data["p"])
    except (signing.BadSignature, KeyError, CannotUndo):
        return None


def add_undo_message(request: HttpRequest, text: str, token: str) -> None:
    """Добавляет тост с «Вернуть»; токен едет в `extra_tags`, шаблон тоста рисует по нему кнопку."""
    messages.success(request, text, extra_tags=token)
