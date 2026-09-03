# Version 2.5 - 15.06.2026 17:05:00 GMT
# Database package для TlibWebApp
# Описание: Пакет для работы с базой данных SQLite
# - connection.py:       Единый помощник открытия соединения open_tlib_db() (read-only)
# - search_executor.py:  Синхронные функции исполнения поискового SQL (count_search, execute_search)
# - query_builder.py:    Координатор построения SQL запросов
# - query_helpers.py:    Вспомогательные функции для запросов
# - query_filters.py:    Построение SQL фильтров
# - reference_loader.py: Загрузка справочных данных
# - update_service.py:   Автообновление БД и управление бэкапами
# - export_utils.py:     Экспорт БД в документы (XLSX)
# - search_limiter.py:   Ограничение параллельных тяжёлых запросов
# 2.5: добавлен экспорт count_search/execute_search из search_executor.py

from .connection import open_tlib_db
from .query_builder import build_search_query
from .reference_loader import (
    load_reference_lists,
    load_redirect_table
)
from .search_limiter import HeavyQueryLimiter, is_light_query
from .search_executor import count_search, execute_search

__all__ = [
    'open_tlib_db',
    'build_search_query',
    'load_reference_lists',
    'load_redirect_table',
    'HeavyQueryLimiter',
    'is_light_query',
    'count_search',
    'execute_search',
]
