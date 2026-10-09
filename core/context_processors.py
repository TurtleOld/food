from django.http import HttpRequest

_SECTIONS = {
    "day": "diary",
    "day_on": "diary",
    "entry_create": "diary",
    "entry_edit": "diary",
    "entry_delete": "diary",
    "product_list": "products",
    "product_create": "products",
    "product_edit": "products",
    "product_delete": "products",
    "daily_target_edit": "target",
}


def nav(request: HttpRequest) -> dict[str, str]:
    """Определяет раздел навигации, который подсвечивается для текущей страницы."""
    match = request.resolver_match
    return {"nav_section": _SECTIONS.get(match.url_name or "", "") if match else ""}
