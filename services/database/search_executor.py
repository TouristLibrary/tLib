# Version 1.0 - 15.06.2026 17:05:00 GMT
# Исполнитель поисковых запросов к tlib.db.
# Описание: Синхронные функции выполнения SQL-поиска, вызываемые через asyncio.to_thread().
#           Открывает соединение с UDF LOWER (кириллица) + CATEGORY_INDEX (порядок сложности),
#           выполняет COUNT и основной SELECT с опциональной пагинацией.
#           Оркестрация конкурентности, лимитер тяжёлых запросов и StreamingResponse
#           остаются в search_router — это ответственность HTTP-слоя.

import sqlite3

from .connection import open_tlib_db


def _open_search_connection(db_path: str, kategoria_list: list) -> sqlite3.Connection:
    """
    Открывает SQLite-соединение с зарегистрированными UDF.

    Регистрирует LOWER (поддержка кириллицы) и CATEGORY_INDEX
    (определение порядкового номера категории по сложности).

    Args:
        db_path:         Путь к файлу БД
        kategoria_list:  Упорядоченный список категорий из app.state

    Returns:
        Открытое соединение sqlite3.Connection с row_factory = sqlite3.Row
    """
    conn = open_tlib_db(db_path, register_lower=True)

    def get_cat_idx(cat):
        """Возвращает индекс категории в упорядоченном списке сложности"""
        if not cat or cat.strip() == '':
            return -1
        try:
            clean_list = [c for c in kategoria_list if c != '']
            return clean_list.index(cat.strip())
        except ValueError:
            return -1

    conn.create_function("CATEGORY_INDEX", 1, get_cat_idx)
    return conn


def count_search(db_path: str, kategoria_list: list, query: str, params: list) -> int:
    """
    Выполняет COUNT-запрос для определения количества результатов.

    Синхронная функция — вызывается через asyncio.to_thread().

    Args:
        db_path:         Путь к файлу БД
        kategoria_list:  Упорядоченный список категорий из app.state
        query:           Базовый SQL-запрос (без COUNT обёртки)
        params:          Параметры запроса

    Returns:
        Количество строк, соответствующих запросу
    """
    conn = _open_search_connection(db_path, kategoria_list)
    try:
        count_query = f"SELECT COUNT(*) FROM ({query})"
        cursor = conn.cursor()
        cursor.execute(count_query, params)
        return cursor.fetchone()[0]
    finally:
        conn.close()


def execute_search(
    db_path: str,
    kategoria_list: list,
    query: str,
    params: list,
    limit: int | None,
    offset: int,
    known_total: int | None
) -> tuple[list, int | None]:
    """
    Выполняет основной поисковый запрос и возвращает результаты.

    Синхронная функция — вызывается через asyncio.to_thread().

    Args:
        db_path:         Путь к файлу БД
        kategoria_list:  Упорядоченный список категорий из app.state
        query:           Базовый SQL-запрос
        params:          Параметры запроса
        limit:           Количество строк на страницу (None — без пагинации)
        offset:          Смещение для пагинации
        known_total:     Уже вычисленный total_count (None — нужно вычислить здесь)

    Returns:
        Кортеж (results, total_count):
          - results: список словарей с данными строк
          - total_count: общее количество строк (None если пагинация не запрашивалась)
    """
    conn = _open_search_connection(db_path, kategoria_list)
    try:
        cursor = conn.cursor()
        total_count = known_total

        if limit is not None:
            # Режим пагинации
            if total_count is None:
                # Лёгкий запрос с пагинацией — считаем total здесь
                count_query = f"SELECT COUNT(*) FROM ({query})"
                cursor.execute(count_query, params)
                total_count = cursor.fetchone()[0]

            paginated_query = query + " LIMIT ? OFFSET ?"
            paginated_params = params + [limit, offset]
            cursor.execute(paginated_query, paginated_params)
        else:
            # Режим без пагинации (обратная совместимость)
            cursor.execute(query, params)

        results = [dict(row) for row in cursor.fetchall()]
        return results, total_count
    finally:
        conn.close()
