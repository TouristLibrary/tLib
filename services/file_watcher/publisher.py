# Version 1.3 - 14.06.2026 13:40:00 GMT
# File Watcher Publisher - Публикация результатов и очистка
# Описание: Этапы 6-7 pipeline - публикация результатов обработки и очистка директорий.
#           - move_group_to_done() перемещает успешные группы из 30_processing/ в data.new/
#           - move_group_to_error() перемещает проблемные группы из 30_processing/ в 40_error/ с созданием .err файла
#           - publish_database() публикует БД (tlib-new.db → assets/tlib-new.db)
#           - cleanup_done_directory() автоудаление файлов из data.new/ (если AUTO_DELETE_DONE_FILES=True)
#           Все перемещения используют shutil.move() для совместимости с cross-device (разные тома/ФС).
# 1.3: publish_database() переведён с TEMP_DATABASE_PATH на DATABASE_NEW_FILE (удалена дубль-константа).

import shutil
from pathlib import Path

from logging_config import app_logger
from .utils import write_error_file


def move_group_to_done(group_id: str) -> bool:
    """
    Перемещает группу из processing/ в done/.
    
    Args:
        group_id: ID группы
        
    Returns:
        True если успешно
    """
    from config import UPLOAD_PROCESSING_DIRECTORY, UPLOAD_DONE_DIRECTORY
    
    processing_dir = Path(UPLOAD_PROCESSING_DIRECTORY)
    done_dir = Path(UPLOAD_DONE_DIRECTORY)
    
    # Находим файлы группы
    group_files = list(processing_dir.glob(f"{group_id}.*"))
    
    if not group_files:
        app_logger.error(
            f"[FILE_WATCHER] Файлы группы {group_id} не найдены в processing/ — "
            f"нарушен инвариант канонизации имён"
        )
        return False

    try:
        for file_path in group_files:
            target_path = done_dir / file_path.name
            shutil.move(str(file_path), str(target_path))
            app_logger.debug(f"[FILE_WATCHER] Перемещен: {file_path.name} → done/")
        
        app_logger.info(
            f"[FILE_WATCHER] Группа {group_id} перемещена в done/ "
            f"({len(group_files)} файлов)"
        )
        return True
        
    except Exception as e:
        app_logger.error(
            f"[FILE_WATCHER] Ошибка перемещения группы {group_id} в done/: {e}", 
            exc_info=True
        )
        return False


def move_group_to_error(group_id: str, error_msg: str) -> bool:
    """
    Перемещает группу из processing/ в error/.
    Создает файл group_id.err с описанием ошибки.
    
    Args:
        group_id: ID группы
        error_msg: Описание ошибки
        
    Returns:
        True если успешно
    """
    from config import UPLOAD_PROCESSING_DIRECTORY, UPLOAD_ERROR_DIRECTORY
    
    processing_dir = Path(UPLOAD_PROCESSING_DIRECTORY)
    error_dir = Path(UPLOAD_ERROR_DIRECTORY)
    
    # Находим файлы группы
    group_files = list(processing_dir.glob(f"{group_id}.*"))
    
    if not group_files:
        app_logger.error(
            f"[FILE_WATCHER] Файлы группы {group_id} не найдены в processing/ — "
            f"нарушен инвариант канонизации имён"
        )
        return False
    
    try:
        # Создаем файл с описанием ошибки
        write_error_file(group_id, "Ошибка перемещения в error/", error_msg, location="error")
        
        # Перемещаем файлы
        for file_path in group_files:
            target_path = error_dir / file_path.name
            shutil.move(str(file_path), str(target_path))
            app_logger.debug(f"[FILE_WATCHER] Перемещен: {file_path.name} → error/")
        
        app_logger.warning(
            f"[FILE_WATCHER] Группа {group_id} перемещена в error/ "
            f"({len(group_files)} файлов): {error_msg}"
        )
        return True
        
    except Exception as e:
        app_logger.error(
            f"[FILE_WATCHER] Ошибка перемещения группы {group_id} в error/: {e}", 
            exc_info=True
        )
        return False


def publish_database() -> bool:
    """
    Атомарно перемещает tlib-new.db → assets/tlib-new.db.
    
    Returns:
        True если успешно
    """
    from config import DATABASE_NEW_FILE, DATABASE_PATH
    
    temp_db = Path(DATABASE_NEW_FILE)
    assets_dir = Path(DATABASE_PATH).parent
    target_db = assets_dir / Path(DATABASE_NEW_FILE).name
    
    if not temp_db.exists():
        app_logger.warning(f"[FILE_WATCHER] Временная БД не найдена: {temp_db}")
        return False
    
    try:
        # shutil.move: пробует os.rename(), при cross-device (EXDEV) делает copy + delete
        shutil.move(str(temp_db), str(target_db))
        
        app_logger.info(
            f"[FILE_WATCHER] БД опубликована: {target_db.name} "
            f"(размер: {target_db.stat().st_size} байт)"
        )
        return True
        
    except Exception as e:
        app_logger.error(
            f"[FILE_WATCHER] Ошибка публикации БД: {e}", 
            exc_info=True
        )
        return False


def cleanup_done_directory() -> int:
    """
    Удаляет файлы из data.new/ (если AUTO_DELETE_DONE_FILES=True).
    
    Returns:
        Количество удаленных файлов
    """
    from config import UPLOAD_DONE_DIRECTORY, AUTO_DELETE_DONE_FILES
    
    if not AUTO_DELETE_DONE_FILES:
        return 0
    
    done_dir = Path(UPLOAD_DONE_DIRECTORY)
    
    if not done_dir.exists():
        return 0
    
    deleted_count = 0
    
    try:
        for file_path in done_dir.iterdir():
            if file_path.is_file():
                file_path.unlink()
                deleted_count += 1
                app_logger.debug(f"[FILE_WATCHER] Удален: done/{file_path.name}")
        
        if deleted_count > 0:
            app_logger.info(f"[FILE_WATCHER] Очищена done/: удалено {deleted_count} файлов")
        
        return deleted_count
        
    except Exception as e:
        app_logger.error(
            f"[FILE_WATCHER] Ошибка очистки done/: {e}", 
            exc_info=True
        )
        return deleted_count
