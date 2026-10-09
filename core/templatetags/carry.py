"""Теги параметров, которые шторка переносит между шагами: значения кодируются, не склеиваются."""

import json
from typing import Any
from urllib.parse import urlencode

from django import template

register = template.Library()


def _present(params: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if value not in ("", None)}


@register.simple_tag
def query_string(**params: Any) -> str:
    """Строка запроса из непустых параметров: `?{% query_string product=1 meal=meal %}`."""
    return urlencode(_present(params))


@register.simple_tag
def hx_vals(**params: Any) -> str:
    """JSON для атрибута `hx-vals`; кавычки экранирует автоэкранирование шаблона."""
    return json.dumps({key: str(value) for key, value in _present(params).items()})
