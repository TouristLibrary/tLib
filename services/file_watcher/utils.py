# Version 1.3 - 15.06.2026 10:18:00 GMT
# File Watcher Utils - Вспомогательные функции
# Описание: Вспомогательные функции для File Watcher pipeline:
#           - initialize_directories() создает структуру директорий (data.up/, 10_up/, 20_go/, 30_processing/, 40_error/, data.old/)
#           - write_error_file() записывает детальный файл ошибки для группы файлов с timestamp, причиной и traceback
#           - normalize_filename_for_data() нормализует имя файла (Шифр→5 цифр, ДопШифр→UPPERCASE)
#           - get_normalized_group_id() нормализует group_id; делегат services/id_utils.py

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from logging_config import app_logger
from services.id_utils import make_norm_id, normalize_group_id


def initialize_directories() -> bool:
    """
    Создает необходимые директории для staged pipeline.
    
    Создает:
    - data.up/
    - data.up/10_up/
    - data.up/20_go/
    - data.up/30_processing/
    - data.up/40_error/
    - data.new/
    - data.old/
    
    Примечание: data.up/pause/ НЕ создаётся автоматически — только вручную администратором.
    
    Returns:
        True если все директории созданы/существуют
    """
    from config import (
        UPLOAD_DIRECTORY,
        UPLOAD_STAGING_DIRECTORY,
        UPLOAD_GO_DIRECTORY,
        UPLOAD_PROCESSING_DIRECTORY,
        UPLOAD_DONE_DIRECTORY,
        UPLOAD_ERROR_DIRECTORY,
        BACKUP_DIRECTORY
    )
    
    directories = [
        UPLOAD_DIRECTORY,
        UPLOAD_STAGING_DIRECTORY,
        UPLOAD_GO_DIRECTORY,
        UPLOAD_PROCESSING_DIRECTORY,
        UPLOAD_DONE_DIRECTORY,
        UPLOAD_ERROR_DIRECTORY,
        BACKUP_DIRECTORY
    ]
    
    try:
        for directory in directories:
            dir_path = Path(directory)
            dir_path.mkdir(parents=True, exist_ok=True)
        
        return True
        
    except Exception as e:
        app_logger.error(f"[FW] Ошибка создания директорий: {e}", exc_info=True)
        return False


def write_error_file(
    group_id: str,
    error_msg: str,
    error_details: str = "",
    traceback_str: str = None,
    location: str = "processing"
) -> Optional[Path]:
    """
    Записывает детальный файл ошибки для группы файлов.
    
    Создает .err файл с подробным описанием ошибки обработки,
    включая timestamp, причину, детали и полный traceback.
    
    Args:
        group_id: ID группы файлов (например "12345-а")
        error_msg: Краткое описание ошибки
        error_details: Детальное описание (опционально)
        traceback_str: Полный traceback если есть (опционально)
        location: Директория размещения - "processing" или "error"
        
    Returns:
        Path к созданному .err файлу или None при ошибке записи
    """
    from config import UPLOAD_PROCESSING_DIRECTORY, UPLOAD_ERROR_DIRECTORY
    
    if location == "processing":
        error_dir = Path(UPLOAD_PROCESSING_DIRECTORY)
    else:
        error_dir = Path(UPLOAD_ERROR_DIRECTORY)
    
    error_file = error_dir / f"{group_id}.err"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    
    content = f"""{'='*60}
ОШИБКА ОБРАБОТКИ FILE WATCHER
{'='*60}
ID группы: {group_id}
Время: {timestamp}
Причина: {error_msg}
"""
    
    if error_details:
        content += f"""
{'-'*60}
ДЕТАЛИ
{'-'*60}
{error_details}
"""
    
    if traceback_str:
        content += f"""
{'-'*60}
TRACEBACK
{'-'*60}
{traceback_str}
"""
    
    try:
        error_file.write_text(content, encoding='utf-8')
        return error_file
    except Exception as e:
        app_logger.error(f"[FW] Не удалось записать .err файл: {e}")
        return None


def normalize_filename_for_data(filename: str) -> str:
    """
    Приводит имя файла к канонической форме: Шифр → 5 цифр, ДопШифр → UPPERCASE.
    Используется при перемещении файлов в 30_processing/ и data/.
    Работает для всех расширений, включая .delete-триггеры.

    Примеры:
    - "12-FRT.json"    → "00012-FRT.json"
    - "12-frt.pdf"     → "00012-FRT.pdf"
    - "1-tst.zip"      → "00001-TST.zip"
    - "1-TST.delete"   → "00001-TST.delete"
    - "345.zip"        → "00345.zip"
    - "00012-FRT.json" → "00012-FRT.json" (без изменений)

    Args:
        filename: Оригинальное имя файла

    Returns:
        Нормализованное имя с Шифром из 5 цифр и ДопШифром в UPPERCASE.
        Если имя не распознано — возвращает оригинал без изменений.
    """
    from .scanner import parse_filename

    parsed = parse_filename(filename)
    if not parsed:
        return filename
    
    shifr = parsed.get("shifr")
    dopshifr = parsed.get("dopshifr")
    ext = parsed.get("ext")

    try:
        base = make_norm_id(shifr, dopshifr or "")
        return f"{base}{ext}"
    except (ValueError, TypeError):
        return filename


def get_normalized_group_id(group_id: str) -> str:
    """
    Нормализует group_id: Шифр → 5 цифр, ДопШифр → UPPERCASE.
    Делегирует в services.id_utils.normalize_group_id; имя сохранено для совместимости.

    Примеры:
    - "12-FRT"    → "00012-FRT"
    - "12-frt"    → "00012-FRT"
    - "345"       → "00345"
    - "00012-FRT" → "00012-FRT"
    - "abc"       → "abc"  (нераспознанный вход — оригинал)
    """
    return normalize_group_id(group_id)
