# Version 2.0 - 06.01.2026 20:29:42 GMT
# JSON Converter Service для TlibWebApp
# Описание: Сервис для конвертации JSON отчетов в SQLite базу данных.
#           Получает список JSON файлов (директория или явный список), валидирует их по JSON Schema (assets/schema.json)
#           через отдельный validation service, и загружает данные в SQLite таблицу (по единому DB-spec).
#           Все ошибки собираются в список и возвращаются вместе со статистикой обработки; поддерживается UTF-8 BOM (utf-8-sig).

import sqlite3
import traceback
from pathlib import Path
from typing import Dict, Tuple, List, Union

from logging_config import app_logger
from config import DATABASE_TABLE_NAME
from .json_schema_validation_service import (
    load_schema,
    read_json_file,
    validate_json_data,
)
from services.database.tlib_table_spec import (
    build_create_table_sql,
    build_insert_sql,
    build_values,
    log_schema_db_drift,
)


def convert_json_to_database(
    json_source: Union[Path, List[Path]], 
    schema_path: Path, 
    output_db: Path, 
    table_name: str = DATABASE_TABLE_NAME
) -> Dict[str, any]:
    """
    Конвертирует JSON файлы в SQLite БД.
    
    Args:
        json_source: Путь к директории с JSON файлами ИЛИ список путей к JSON файлам
        schema_path: Путь к схеме для валидации
        output_db: Путь к выходной БД
        table_name: Имя таблицы в БД (по умолчанию "tlib")
        
    Returns:
        {
            "success_count": int,        # Количество успешно обработанных файлов
            "total_count": int,          # Общее количество JSON файлов
            "errors": [                  # Список ошибок
                {
                    "file": str,         # Имя файла
                    "error": str,        # Описание ошибки
                    "traceback": str     # Полный traceback
                }
            ]
        }
    """
    app_logger.info(f"[JSON_CONVERTER] Начало конвертации JSON → SQLite")
    
    # Форматируем вывод источника в зависимости от типа
    if isinstance(json_source, Path):
        app_logger.info(f"[JSON_CONVERTER]   Источник: директория {json_source}")
    else:
        # Список файлов - показываем первые 3 + общее количество
        file_names = [p.name for p in json_source[:3]]
        if len(json_source) > 3:
            app_logger.info(f"[JSON_CONVERTER]   Источник: {len(json_source)} файлов ({', '.join(file_names)}, ...)")
        else:
            app_logger.info(f"[JSON_CONVERTER]   Источник: {', '.join(file_names)}")
    
    app_logger.info(f"[JSON_CONVERTER]   База данных: {output_db}")
    app_logger.info(f"[JSON_CONVERTER]   Схема: {schema_path}")
    
    # Инициализация результата
    result = {
        "success_count": 0,
        "total_count": 0,
        "errors": []
    }
    
    try:
        # Загружаем schema один раз (с кэшем) и делаем drift-check (WARNING-only)
        schema = load_schema(schema_path)
        log_schema_db_drift(schema, schema_name=str(schema_path))

        # Получаем список JSON файлов
        if isinstance(json_source, Path):
            # Директория - ищем все JSON
            json_files = list(json_source.glob("*.json"))
        else:
            # Список файлов - используем как есть
            json_files = [f for f in json_source if f.suffix.lower() == '.json']
        
        result["total_count"] = len(json_files)
        
        if not json_files:
            app_logger.warning(f"[JSON_CONVERTER] Нет JSON файлов")
            return result
        
        app_logger.info(f"[JSON_CONVERTER] Найдено {len(json_files)} JSON файлов")
        
        # Удаляем старую базу если существует
        if output_db.exists():
            output_db.unlink()
            app_logger.debug(f"[JSON_CONVERTER] Удалена старая БД: {output_db}")
        
        # Создаем подключение к БД
        conn = sqlite3.connect(output_db)
        cursor = conn.cursor()
        
        # Создаем таблицу на основе единого DB-spec
        cursor.execute(build_create_table_sql(table_name))
        app_logger.debug(f"[JSON_CONVERTER] Создана таблица {table_name}")

        insert_sql = build_insert_sql(table_name)
        
        # Обрабатываем каждый JSON файл
        for json_file in json_files:
            try:
                # Читаем JSON один раз
                data = read_json_file(json_file)

                # Валидация dict по схеме (без повторного чтения schema)
                is_valid, error_msg = validate_json_data(data=data, schema=schema, json_name=json_file.name)
                if not is_valid:
                    result["errors"].append(
                        {"file": json_file.name, "error": error_msg, "traceback": ""}
                    )
                    continue

                # Вставка в БД по единому DB-spec
                values = build_values(data)
                cursor.execute(insert_sql, values)
                result["success_count"] += 1
                app_logger.debug(f"[JSON_CONVERTER] Успешно: {json_file.name}")
                
            except Exception as e:
                error_detail = traceback.format_exc()
                result["errors"].append({
                    "file": json_file.name,
                    "error": str(e),
                    "traceback": error_detail
                })
                app_logger.error(
                    f"[JSON_CONVERTER] Ошибка обработки {json_file.name}: {e}", 
                    exc_info=True
                )
        
        # Коммит изменений
        conn.commit()
        conn.close()
        
        app_logger.info(
            f"[JSON_CONVERTER] Конвертация завершена: "
            f"успешно={result['success_count']}, "
            f"ошибок={len(result['errors'])}, "
            f"всего={result['total_count']}"
        )
        
    except Exception as e:
        error_msg = f"Критическая ошибка конвертации: {e}"
        app_logger.error(f"[JSON_CONVERTER] {error_msg}", exc_info=True)
        result["errors"].append({
            "file": "SYSTEM",
            "error": error_msg,
            "traceback": traceback.format_exc()
        })
    
    return result


def get_file_id_from_json_name(json_filename: str) -> str:
    """
    Извлекает ID файла из имени JSON файла.
    
    Args:
        json_filename: Имя файла (например "12345-а.json" или "12345.json")
        
    Returns:
        ID файла без расширения ("12345-а" или "12345")
    """
    return Path(json_filename).stem
