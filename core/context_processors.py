from django.http import HttpRequest

_SECTION_BY_PREFIX = {
    "": "diary",
    "day": "diary",
    "entries": "diary",
    "undo": "diary",
    "products": "products",
    "barcodes": "products",
    "target": "target",
}


def nav(request: HttpRequest) -> dict[str, str]:
    """Определяет раздел навигации по первому сегменту пути, чтобы новые маршруты не терялись."""
    if request.resolver_match is None:
        return {"nav_section": ""}
    prefix = request.path.strip("/").split("/")[0]
    return {"nav_section": _SECTION_BY_PREFIX.get(prefix, "")}
