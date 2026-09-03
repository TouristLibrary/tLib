# Version 1.0 - 15.06.2026 10:18:00 GMT
# Единая нормализация идентификаторов отчётов для TlibWebApp.
# Описание: Каноничные примитивы приведения Шифра и ДопШифра к нормализованному виду.
#           Шифр → строка минимум 5 цифр с ведущими нулями.
#           ДопШифр → strip + UPPERCASE.
#           Используется upload_service, file_watcher и ops-скриптами tools/.
#           Модуль не импортирует другие сервисы проекта (исключает циклические зависимости).


def normalize_shifr_to_5digits(shifr) -> str:
    """
    Нормализует Шифр до строки минимум 5 цифр с ведущими нулями.

    Примеры:
      12     → "00012"
      345    → "00345"
      12345  → "12345"
      123456 → "123456"  (более 5 цифр сохраняются)

    Args:
        shifr: Числовое значение Шифра (int или строковое представление числа).

    Returns:
        Строка с Шифром (минимум 5 цифр).

    Raises:
        ValueError: если shifr не приводится к int.
        TypeError: если shifr имеет несовместимый тип.
    """
    return str(int(shifr)).zfill(5)


def normalize_dopshifr(dop: str) -> str:
    """
    Нормализует ДопШифр: убирает пробелы по краям и приводит к UPPERCASE.

    Для валидированных входов (DOP_SHIFR_RE в upload) strip — no-op.

    Args:
        dop: Строка ДопШифра или None/пустая строка.

    Returns:
        ДопШифр в UPPERCASE без пробелов, или пустая строка.
    """
    return (dop or "").strip().upper()


def make_norm_id(shifr, dop: str) -> str:
    """
    Собирает каноничный нормализованный ID из Шифра и ДопШифра.

    Примеры:
      (12, "tlib")  → "00012-TLIB"
      (345, "")     → "00345"
      (1, None)     → "00001"

    Args:
        shifr: Числовое значение Шифра.
        dop: ДопШифр (может быть None или пустой строкой).

    Returns:
        Нормализованный ID вида "NNNNN-DOP" или "NNNNN".

    Raises:
        ValueError / TypeError: если shifr не является числом.
    """
    shifr5 = normalize_shifr_to_5digits(shifr)
    dopu = normalize_dopshifr(dop)
    return f"{shifr5}-{dopu}" if dopu else shifr5


def normalize_group_id(group_id: str) -> str:
    """
    Нормализует group_id вида "shifr-dop" или "shifr".

    Примеры:
      "12-FRT"    → "00012-FRT"
      "12-frt"    → "00012-FRT"
      "345"       → "00345"
      "00012-FRT" → "00012-FRT"  (идемпотентно)
      "abc"       → "abc"        (нераспознанный вход → оригинал)

    Args:
        group_id: Строка идентификатора группы.

    Returns:
        Нормализованный ID или исходная строка при ошибке.
    """
    shifr_str, _, dop = group_id.partition("-")
    try:
        return make_norm_id(shifr_str, dop)
    except (ValueError, TypeError):
        return group_id
