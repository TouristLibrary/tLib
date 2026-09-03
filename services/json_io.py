# Version 1.0 - 04.06.2026 00:00:00 GMT
# JSON IO для TlibWebApp
# Описание: Централизованное чтение JSON-файлов отчётов с BOM-устойчивым декодированием.
#           read_json() — единственная точка входа для чтения JSON: кодек utf-8-sig
#           прозрачно срезает UTF-8 BOM (EF BB BF), если он есть, и читает как UTF-8, если нет.
#           Это позволяет корректно читать легаси-отчёты, сохранённые с BOM, без конвертации файлов.
#           Записи файлов намеренно остаются на encoding='utf-8' (без BOM) — new/edited отчёты
#           самоисцеляются от BOM при первом сохранении через edit-flow.

import json
from pathlib import Path
from typing import Any


def read_json(path: str | Path) -> Any:
    """
    Читает и парсит JSON-файл с BOM-устойчивым декодированием.

    Использует кодек utf-8-sig, который прозрачно срезает необязательный UTF-8 BOM
    (байты EF BB BF) в начале файла, если он присутствует. Без BOM ведёт себя
    идентично обычному utf-8.

    Покрывает два класса файлов:
    - Новые отчёты (записанные json.dumps + encoding='utf-8') — BOM отсутствует.
    - Легаси-отчёты, мигрированные или отредактированные в Windows-редакторах с BOM.

    Args:
        path: Путь к JSON-файлу (str или Path).

    Returns:
        Распарсенный объект Python (dict, list и т.п.).

    Raises:
        FileNotFoundError: если файл не найден.
        json.JSONDecodeError: если содержимое не является валидным JSON.
        UnicodeDecodeError: если файл не является корректным UTF-8 (с BOM или без).
    """
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))
