# Version 1.1 - 07.01.2026 13:33:25 GMT
# Update Service - Управление обновлениями и бэкапами БД
# Описание: Модуль для критической системной работы с базой данных: валидация, бэкапы, автообновление.
#           validate_sqlite_database() проверяет что файл является валидной SQLite базой данных.
#           cleanup_old_backups() удаляет бэкапы БД старше заданного количества дней (ручное использование).
#           perform_database_update() выполняет автообновление БД при появлении файла-триггера (tlib-new.db),
#           создаёт бэкап текущей базы в data.old/, атомарно заменяет БД, обновляет кэш в app.state,
#           и обновляет app.state.reference_version для сигнализации фронтенду о смене справочников.
#           Экспорт в XLSX изолирован в отдельный модуль и вызывается с обработкой ошибок.
#           Все timestamp бэкапов используют UTC+0.

import logging
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from logging_config import app_logger, log_with_data
from config import BACKUP_TIMESTAMP_FORMAT, DATABASE_BACKUP_PREFIX, BACKUP_DIRECTORY, XLSX_EXPORT_FILENAME
from config import STATE_KATEGORIA_UNIFIED, STATE_REPORTS_COUNT
from .reference_loader import load_reference_lists, load_redirect_table


def validate_sqlite_database(db_path: Path) -> bool:
    """
    Проверяет, что файл является валидной SQLite базой данных.
    
    Выполняет проверку заголовка файла и пробует открыть базу и выполнить запрос.
    
    Args:
        db_path: Путь к файлу базы данных
        
    Returns:
        bool: True если файл является валидной SQLite базой, False иначе
    """
    try:
        # Проверка существования файла
        if not db_path.exists():
            app_logger.error(f"Файл не существует: {db_path}")
            return False
        
        # Проверка магического заголовка SQLite (первые 16 байт)
        with open(db_path, 'rb') as f:
            header = f.read(16)
            if not header.startswith(b'SQLite format 3'):
                app_logger.error(f"Файл не является SQLite базой: {db_path}")
                return False
        
        # Пробуем открыть и выполнить запрос
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1")
        cursor.fetchone()
        conn.close()
        
        return True
        
    except Exception as e:
        app_logger.error(f"Ошибка валидации SQLite базы {db_path}: {e}")
        return False


def perform_database_update(db_dir: Path, app_state, db_path: str, backup_pattern: str, retention_days: int, new_file_name: str) -> bool:
    """
    Выполняет автоматическое обновление базы данных.
    
    Процесс:
    1. Проверяет наличие файла-триггера (tlib-new.db)
    2. Валидирует новый файл как SQLite базу
    3. Создаёт бэкап текущей базы с timestamp в data.old/
    4. Атомарно заменяет базу данных
    5. Обновляет кэш в app.state
    6. Экспортирует базу данных в XLSX (с обработкой ошибок)
    
    Args:
        db_dir: Директория с базой данных
        app_state: Объект app.state для обновления кэша
        db_path: Путь к рабочей базе данных
        backup_pattern: Паттерн имени бэкапа (strftime формат)
        retention_days: Количество дней хранения бэкапов
        new_file_name: Имя файла-триггера для обновления
        
    Returns:
        bool: True если обновление выполнено успешно, False иначе
    """
    new_db_path = db_dir / new_file_name
    current_db_path = Path(db_path)
    
    # Создаем директорию для бэкапов БД
    backup_dir = Path(BACKUP_DIRECTORY)
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    # Проверяем наличие файла-триггера
    if not new_db_path.exists():
        return False
    
    try:
        # 1. Валидация нового файла
        if not validate_sqlite_database(new_db_path):
            app_logger.error(f"Файл {new_file_name} не является валидной SQLite БД")
            new_db_path.unlink()
            return False
        
        # 2. Создание бэкапа текущей базы
        timestamp = datetime.now(timezone.utc).strftime(BACKUP_TIMESTAMP_FORMAT)
        backup_name = f"{DATABASE_BACKUP_PREFIX}_{timestamp}.db"
        backup_path = backup_dir / backup_name
        
        if current_db_path.exists():
            shutil.copy2(str(current_db_path), str(backup_path))
        
        # 3. Атомарная замена базы данных
        new_db_path.replace(current_db_path)
        
        # 4. Обновление кэша в app.state
        reference_lists = load_reference_lists(db_path)
        
        app_state.dopshifr_list = reference_lists['dopshifr_list']
        app_state.raion_obshiy_list = reference_lists['raion_obshiy_list']
        app_state.tip_list = reference_lists['tip_list']
        app_state.kategoria_s_list = reference_lists['kategoria_s_list']
        app_state.kategoria_po_list = reference_lists['kategoria_po_list']
        app_state.kategoria_unified_list = reference_lists[STATE_KATEGORIA_UNIFIED]
        app_state.reports_count = reference_lists[STATE_REPORTS_COUNT]
        app_state.reference_version = datetime.now(timezone.utc).isoformat()
        
        # Обновляем таблицу редиректов
        app_state.redirect_table = load_redirect_table(db_path)
        
        # 5. Экспорт базы данных в XLSX (с обработкой ошибок)
        xlsx_path = db_dir / XLSX_EXPORT_FILENAME
        try:
            from .export_utils import export_database_to_xlsx
            export_database_to_xlsx(db_path, str(xlsx_path))
        except ImportError as e:
            app_logger.warning(f"Экспорт XLSX недоступен (openpyxl не установлен): {e}")
        except Exception as e:
            app_logger.warning(f"Ошибка экспорта XLSX (БД обновлена успешно): {e}")
        
        log_with_data(logging.INFO, "БД обновлена",
                     reports=reference_lists[STATE_REPORTS_COUNT],
                     backup=backup_name)
        
        return True
        
    except Exception as e:
        log_with_data(logging.ERROR, "Ошибка автообновления БД",
                     error=str(e),
                     file=new_file_name)
        app_logger.error(str(e), exc_info=True)
        return False
