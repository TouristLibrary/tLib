# Version 1.2 - 14.06.2026 13:40:00 GMT
# File Watcher Database Generator - Генерация и проверка БД
# Описание: Этап 5 pipeline - генерация и финальная проверка базы данных.
#           - generate_final_database_check() финальная проверка всего батча (БД из data/ + успешные группы из processing/)
#             с нормализацией имён для корректного исключения старых версий (предотвращение дубликатов)
#           - generate_database_from_data() генерация БД из всех JSON файлов в data/ с дедупликацией по нормализованному id
#           Финальная проверка страхует от редких SQLite-специфичных ошибок (~1-5%).
# 1.2: generate_final_database_check() переведён с TEMP_DATABASE_PATH на DATABASE_NEW_FILE.

from pathlib import Path
from typing import Dict, List

from logging_config import app_logger
from services.validation.json_converter_service import convert_json_to_database, get_file_id_from_json_name


def generate_final_database_check(successful_group_ids: List[str]) -> Dict[str, any]:
    """
    Финальная проверка: БД из data/ + ВСЕ успешные группы из processing/.
    Вызывается ПЕРЕД копированием батча в data/.
    
    Args:
        successful_group_ids: Список ID групп, прошедших Проверку №2
        
    Returns:
        {
            "success": bool,
            "error_files": [str],
            "errors": [{"file": str, "error": str, "traceback": str}]
        }
    """
    from config import DATA_DIRECTORY, UPLOAD_PROCESSING_DIRECTORY, SCHEMA_PATH, DATABASE_NEW_FILE, DATABASE_TABLE_NAME
    
    data_dir = Path(DATA_DIRECTORY)
    processing_dir = Path(UPLOAD_PROCESSING_DIRECTORY)
    
    # Импорт функции нормализации для корректного сравнения
    from .utils import get_normalized_group_id
    
    # Собираем список файлов для финальной БД
    json_files = []
    
    # Нормализуем successful_group_ids для правильного сравнения (без ведущих нулей)
    normalized_successful_ids = {get_normalized_group_id(gid) for gid in successful_group_ids}
    
    # 1. Все JSON из data/ кроме successful_group_ids (с нормализацией)
    for json_file in data_dir.glob("*.json"):
        file_id = get_file_id_from_json_name(json_file.name)
        normalized_file_id = get_normalized_group_id(file_id)
        
        if normalized_file_id not in normalized_successful_ids:
            json_files.append(json_file)
        else:
            # Старый файл исключается, будет заменен новым из processing/
            app_logger.debug(
                f"[FILE_WATCHER] Исключен старый файл {json_file.name} "
                f"(будет заменен новым из processing/)"
            )
    
    # 2. Все успешные группы из processing/
    for group_id in successful_group_ids:
        new_json = processing_dir / f"{group_id}.json"
        if new_json.exists():
            json_files.append(new_json)
    
    app_logger.info(
        f"[FILE_WATCHER] Финальная проверка батча: "
        f"{len(json_files)} JSON (старые + {len(successful_group_ids)} новых)"
    )
    
    # Финальная сборка БД
    result = convert_json_to_database(
        json_source=json_files,
        schema_path=Path(SCHEMA_PATH),
        output_db=Path(DATABASE_NEW_FILE),
        table_name=DATABASE_TABLE_NAME
    )
    
    # Формируем список file_id с ошибками
    error_files = []
    for error in result["errors"]:
        file_id = get_file_id_from_json_name(error["file"])
        error_files.append(file_id)
    
    return {
        "success": result["success_count"] > 0 and len(result["errors"]) == 0,
        "error_files": error_files,
        "errors": result["errors"]
    }
