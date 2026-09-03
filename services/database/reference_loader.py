# Version 1.0 - 23.12.2025 18:42:14 GMT
# Reference Loader для загрузки справочных данных
# Описание: Модуль для загрузки справочных данных из базы данных SQLite.
#           load_reference_lists() загружает все справочные списки (ДопШифр, РайонОбщий, Тип, КатегорияС, КатегорияПо)
#           и создаёт объединённый список категорий с сортировкой по сложности.
#           load_redirect_table() загружает таблицу редиректов из поля СтарыйID в БД.
#           Используется при инициализации приложения для кэширования данных в app.state.

import sqlite3
from pathlib import Path
from logging_config import app_logger
from config import DATABASE_TABLE_NAME
from .query_helpers import category_sort_key
from config import REFERENCE_FIELDS, get_db_columns, get_default_reference_values, STATE_KATEGORIA_UNIFIED, STATE_REPORTS_COUNT


# === Константы модуля ===

ALLOWED_COLUMNS = get_db_columns()


def load_reference_lists(db_path: str) -> dict:
    """
    Загружает все справочные списки из базы данных
    
    Функция извлекает уникальные значения для всех справочных полей
    (ДопШифр, РайонОбщий, Тип, КатегорияС, КатегорияПо) из базы данных.
    Используется при инициализации приложения для кэширования данных в app.state.
    
    Args:
        db_path: Путь к файлу базы данных SQLite
        
    Returns:
        dict: Словарь с загруженными списками
              Ключи: dopshifr_list, raion_obshiy_list, tip_list, 
                     kategoria_s_list, kategoria_po_list, kategoria_unified_list
              Значения: списки строк с уникальными значениями
    
    Примечание:
        - Для ДопШифр добавляются префиксные значения ["", "нет"]
        - Для остальных полей добавляется только [""]
        - При ошибке возвращаются списки по умолчанию
        - Использует LOWER() для сортировки (требуется регистрация функции в SQLite)
    """
    # Инициализация словаря со списками из конфигурации
    lists = {config['state_key']: [] for config in REFERENCE_FIELDS.values()}
    lists[STATE_KATEGORIA_UNIFIED] = []
    lists[STATE_REPORTS_COUNT] = 0
    
    db_path_obj = Path(db_path)
    
    # Проверка существования базы данных
    if not db_path_obj.exists():
        app_logger.warning(f"База данных не найдена: {db_path}")
        # Возвращаем значения по умолчанию
        return get_default_reference_values()
    
    try:
        # Подключаемся к базе данных
        conn = sqlite3.connect(str(db_path))
        
        # Регистрируем функцию LOWER для корректной работы с кириллицей
        conn.create_function("LOWER", 1, lambda s: s.lower() if s else s)
        cursor = conn.cursor()
        
        # Загружаем данные для каждого справочного поля
        for field_config in REFERENCE_FIELDS.values():
            column = field_config['db_column']
            prefix = field_config['default_prefix']
            state_key = field_config['state_key']
            
            # Защита от SQL injection: проверяем имя столбца
            assert column in ALLOWED_COLUMNS, f"Invalid column: {column}"
            
            cursor.execute(f"""
                SELECT DISTINCT {column}
                FROM {DATABASE_TABLE_NAME} 
                WHERE {column} IS NOT NULL 
                  AND TRIM({column}) != '' 
                ORDER BY LOWER({column}) ASC
            """)
            
            # Извлекаем значения и объединяем с префиксом
            values = [row[0].strip() for row in cursor.fetchall()]
            lists[state_key] = prefix + values
            
            app_logger.debug(f"✓ Загружено {len(values)} значений {column}")
        
        # Создаем объединенный список категорий (без пустых значений, с сортировкой)
        all_categories = set(lists['kategoria_s_list']) | set(lists['kategoria_po_list'])
        all_categories.discard('')  # Убираем пустую строку
        # Сортируем с учетом кириллицы
        sorted_categories = sorted(all_categories, key=category_sort_key)
        lists[STATE_KATEGORIA_UNIFIED] = [''] + sorted_categories
        app_logger.debug(f"✓ Объединенный список категорий: {len(sorted_categories)} значений")
        
        # Подсчитываем общее количество отчетов
        cursor.execute(f"SELECT COUNT(*) FROM {DATABASE_TABLE_NAME}")
        lists[STATE_REPORTS_COUNT] = cursor.fetchone()[0]
        app_logger.debug(f"✓ Всего отчетов в БД: {lists[STATE_REPORTS_COUNT]}")
        
        conn.close()
        
    except Exception as e:
        app_logger.error(f"Ошибка при загрузке справочных данных: {e}", exc_info=True)
        # При ошибке возвращаем значения по умолчанию
        return get_default_reference_values()
    
    return lists


def load_redirect_table(db_path: str) -> dict:
    """
    Загружает таблицу редиректов из базы данных.
    Строит маппинг: "id={СтарыйID}" → "{Шифр}-{ДопШифр}" или "{Шифр}"
    
    Args:
        db_path: Путь к файлу базы данных SQLite
        
    Returns:
        dict: Словарь с маппингом ключей редиректов на целевые шифры
    """
    redirect_table = {}
    db_path_obj = Path(db_path)
    
    if not db_path_obj.exists():
        app_logger.warning(f"База данных не найдена: {db_path}")
        return redirect_table
    
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        # Выбираем записи, где СтарыйID заполнен
        cursor.execute(f"""
            SELECT СтарыйID, Шифр, ДопШифр 
            FROM {DATABASE_TABLE_NAME} 
            WHERE СтарыйID IS NOT NULL
        """)
        
        for row in cursor.fetchall():
            old_id, shifr, dop_shifr = row
            key = f"id={old_id}"
            
            # Формируем целевой шифр: Шифр-ДопШифр или просто Шифр
            if dop_shifr and str(dop_shifr).strip():
                value = f"{shifr}-{dop_shifr.strip()}"
            else:
                value = str(shifr)
            
            redirect_table[key] = value
        
        conn.close()
        app_logger.info(f"Таблица редиректов загружена из БД: {len(redirect_table)} записей")
        
    except Exception as e:
        app_logger.error(f"Ошибка загрузки таблицы редиректов: {e}", exc_info=True)
    
    return redirect_table
