"""Безопасное отображение Markdown только в ответах Ассистента."""

import re

from django import template
from django.utils.safestring import SafeString, mark_safe
from markdown_it import MarkdownIt

register = template.Library()


class _ChatMarkdown(MarkdownIt):
    def validateLink(self, url: str) -> bool:
        """Разрешает веб-ссылки, почту и относительные адреса."""
        return (
            re.match(r"^(?!https?:|mailto:)[a-z][a-z0-9+.-]*:", url.strip(), re.IGNORECASE) is None
        )


_parser = _ChatMarkdown("commonmark", {"html": False, "breaks": True}).enable("table")


@register.filter
def markdown(value: str) -> SafeString:
    """Экранирует HTML, запрещает опасные ссылки и отображает разметку ответа."""
    return mark_safe(_parser.render(value))
