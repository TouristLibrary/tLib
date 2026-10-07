# Version 1.1 - 07.10.2026 06:55:56 GMT
# Единая точка открытия соединения с основной БД tlib.db для API-слоя.
# Описание: Предоставляет open_tlib_db() — единый помощник подключения к tlib.db.
#           По умолчанию открывает БД в режиме read-only, закрепляя инвариант проекта:
#           запись в tlib.db идёт исключительно через File Watcher.
#           Опциональные флаги: row_factory (sqlite3.Row), register_lower (UDF для кириллицы).
# 1.1: UDF LOWER (register_lower, его берёт только поиск) сворачивает ё в е: ё есть в 11 % маршрутов,
#      и «озер» не находил «Озёрный». Справочники и SEO регистрируют свой LOWER — их не затрагивает.

import sqlite3
from pathlib import Path

from config import DATABASE_PATH, SQLITE_CONNECT_TIMEOUT


def open_tlib_db(
    path: str = DATABASE_PATH,
    *,
    read_only: bool = True,
    row_factory: bool = True,
    register_lower: bool = False,
    timeout: float = SQLITE_CONNECT_TIMEOUT,
) -> sqlite3.Connection:
    """
    Открывает соединение с tlib.db.

    По умолчанию read-only + row_factory=sqlite3.Row.
    Кросс-платформенный URI для read-only строится через Path.resolve().as_uri()
    (корректно работает на Windows и Linux, в том числе с временными путями тестов).

    Args:
        path:            Путь к файлу БД (по умолчанию DATABASE_PATH).
        read_only:       Открыть в режиме read-only (uri?mode=ro). По умолчанию True.
        row_factory:     Установить conn.row_factory = sqlite3.Row. По умолчанию True.
        register_lower:  Зарегистрировать UDF LOWER для поиска: кириллица в нижнем регистре,
                         ё свёрнута в е. По умолчанию False.
        timeout:         Таймаут ожидания снятия блокировки (секунды).

    Returns:
        sqlite3.Connection с применёнными флагами.
    """
    if read_only:
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout)
    else:
        conn = sqlite3.connect(path, timeout=timeout)

    if row_factory:
        conn.row_factory = sqlite3.Row

    if register_lower:
        # Обе стороны сравнения в поиске идут через LOWER (LOWER(поле) LIKE LOWER(?)), поэтому
        # свёртка ё → е находит «озёр» по «озер» и обратно. Ё становится ё на lower()
        conn.create_function("LOWER", 1, lambda s: s.lower().replace("ё", "е") if s else s)

    return conn
