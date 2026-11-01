"""Фото хода Ассистента: проверка, сжатие и удаление после ответа.

Фото уходит внешнему провайдеру, поэтому EXIF (в том числе GPS) срезается
перекодированием, а байты JPEG живут в БД только до конца хода.
"""

import hashlib
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from django.conf import settings
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
# Потолок пикселей: маленький PNG может распаковываться в гигабайты памяти.
MAX_PIXELS = 24_000_000
# PyAV/EXIF не нужны: Pillow читает формат и перекодирует пиксели.
PHOTO_ERROR = "Не удалось прочитать фото. Приложите JPEG, PNG или WebP."


class PhotoError(ValueError):
    """Фото не подходит по формату, размеру или содержимому."""


@dataclass(frozen=True)
class PreparedPhoto:
    """Проверенное и перекодированное фото, готовое к отправке модели."""

    content: bytes
    digest: str


def _megabytes(limit: int) -> int:
    return max(1, limit // (1024 * 1024))


def prepare_photo(upload: Any) -> PreparedPhoto:
    """Проверяет фото и перекодирует его в JPEG без EXIF.

    Args:
        upload: загруженный файл из формы.

    Returns:
        Байты JPEG и их отпечаток для идемпотентности хода.

    Raises:
        PhotoError: файл пустой, слишком большой, не картинка или формат не разрешён.
    """
    limit = settings.ASSISTANT_PHOTO_MAX_BYTES
    size = getattr(upload, "size", 0) or 0
    if size <= 0:
        raise PhotoError("Фото пустое. Приложите другой файл.")
    if size > limit:
        raise PhotoError(f"Фото больше {_megabytes(limit)} МБ. Приложите файл меньше.")
    # Читаем не больше лимита: иначе огромный файл попадёт в память целиком.
    data = upload.read(limit + 1)
    if len(data) > limit:
        raise PhotoError(f"Фото больше {_megabytes(limit)} МБ. Приложите файл меньше.")
    try:
        with Image.open(BytesIO(data)) as probe:
            image_format = probe.format
            if image_format not in ALLOWED_FORMATS:
                raise PhotoError("Поддерживаются только фото JPEG, PNG или WebP.")
            if probe.width * probe.height > MAX_PIXELS:
                raise PhotoError("Фото слишком большое по разрешению. Приложите фото поменьше.")
            probe.verify()
        with Image.open(BytesIO(data)) as source:
            image = ImageOps.exif_transpose(source)
            if image.mode != "RGB":
                image = image.convert("RGB")
            image.thumbnail((settings.ASSISTANT_PHOTO_MAX_SIDE, settings.ASSISTANT_PHOTO_MAX_SIDE))
            output = BytesIO()
            image.save(output, format="JPEG", quality=85, optimize=True)
    except PhotoError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        raise PhotoError(PHOTO_ERROR) from None
    content = output.getvalue()
    return PreparedPhoto(content=content, digest=hashlib.sha256(content).hexdigest())
