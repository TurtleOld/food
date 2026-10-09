"""Проверка и нормализация Штрихкода: чистые функции без обращения к базе."""

_VALID_LENGTHS = (8, 12, 13)


def _check_digit_is_valid(digits: str) -> bool:
    body, check = digits[:-1], int(digits[-1])
    # Вес 3 у цифр, считая справа от контрольной: так сумма одинакова для EAN-8, UPC-A и EAN-13.
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def normalize_barcode(raw: str) -> str | None:
    """Приводит введённый или отсканированный код к хранимому виду.

    Принимает 8, 12 или 13 цифр с верной контрольной цифрой (пробелы игнорируются).
    UPC-A дополняется нулём до EAN-13: скан и ручной ввод одного кода находят одну запись.
    Возвращает `None`, если код неполный или контрольная цифра неверна.
    """
    digits = "".join(raw.split())
    if not (digits.isascii() and digits.isdigit()) or len(digits) not in _VALID_LENGTHS:
        return None
    if not _check_digit_is_valid(digits):
        return None
    return digits.zfill(13) if len(digits) == 12 else digits
