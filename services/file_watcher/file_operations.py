# Version 2.3 - 26.07.2026 09:00:00 GMT
# File Watcher File Operations - Файловые операции
# Описание: Модуль файловых операций (side effects) для File Watcher pipeline.
#           - move_group_to_processing() атомарно перемещает группу файлов в data.up/30_processing/
#             с нормализацией имён (Шифр → 5 цифр, ДопШифр → UPPERCASE) и откатом при ошибке
#           - check_data_conflicts() проверяет конфликты в data/ по нормализованному имени (5 цифр)
#             и возвращает только файлы с совпадающими расширениями
#           - backup_to_old() перемещает файлы в data.old/ с timestamp (формат: name_YYYYMMDD_HHMMSSmmm.ext)
#             используя shutil.move для cross-device совместимости
#           - copy_processing_to_data() копирует файлы из 30_processing/ в data/ с нормализацией имен
#             (Шифр приводится к 5 цифрам: 12-FRT → 00012-FRT)
#           - process_partial_group() обрабатывает группы без JSON (только PDF/ZIP)
#             копирует файлы в data/ БЕЗ перестроения БД
#           - has_json_file() проверяет наличие JSON файла в processing/ для группы
#           - canonicalize_json_dopshifr() приводит поле ДопШифр в JSON к UPPERCASE (no-op при совпадении)
#           Все операции логируются и поддерживают откат при ошибках.

import shutil
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

from logging_config import app_logger


def move_group_to_processing(group_id: str, files: List[Path]) -> bool:
    """
    Атомарно перемещает группу файлов в data.up/30_processing/.

    Имена файлов нормализуются при перемещении: Шифр приводится к 5 цифрам,
    ДопШифр — к UPPERCASE (например, 1-tst.json → 00001-TST.json).
    Это гарантирует, что все последующие шаги pipeline работают с каноническими
    именами независимо от оригинального написания.

    При ошибке откатывает все перемещения.

    Args:
        group_id: ID группы (уже нормализованный)
        files: Список файлов группы (оригинальные пути из 20_go/)

    Returns:
        True если успешно, False при ошибке
    """
    from config import UPLOAD_PROCESSING_DIRECTORY
    from .utils import normalize_filename_for_data

    processing_dir = Path(UPLOAD_PROCESSING_DIRECTORY)
    moved_files = []

    try:
        for file_path in files:
            normalized_name = normalize_filename_for_data(file_path.name)
            target_path = processing_dir / normalized_name

            file_path.replace(target_path)
            moved_files.append((file_path, target_path))

            if file_path.name != normalized_name:
                app_logger.info(
                    f"[FILE_WATCHER] Переименован при move: {file_path.name} → processing/{normalized_name}"
                )
            else:
                app_logger.debug(f"[FILE_WATCHER] Перемещен: {file_path.name} → processing/")

        app_logger.info(
            f"[FILE_WATCHER] Группа {group_id} перемещена в processing/ "
            f"({len(files)} файлов)"
        )
        return True

    except Exception as e:
        app_logger.error(
            f"[FILE_WATCHER] Ошибка перемещения группы {group_id}: {e}",
            exc_info=True
        )

        # Откат: возвращаем перемещённые файлы по исходным путям
        for original_path, moved_path in moved_files:
            try:
                if moved_path.exists():
                    moved_path.replace(original_path)
                    app_logger.debug(f"[FILE_WATCHER] Откат: {moved_path.name}")
            except Exception as rollback_error:
                app_logger.error(
                    f"[FILE_WATCHER] Ошибка отката {moved_path.name}: {rollback_error}"
                )

        return False


def check_data_conflicts(group_id: str) -> List[Path]:
    """
    Проверяет наличие файлов в data/ по нормализованному имени (5 цифр).
    Возвращает ТОЛЬКО те файлы, для которых есть новые версии в processing/.
    
    Args:
        group_id: ID группы (может быть "12-FRT" или "00012-FRT")
        
    Returns:
        Список существующих файлов, для которых есть новые версии (по расширению)
    """
    from config import DATA_DIRECTORY, UPLOAD_PROCESSING_DIRECTORY
    from .utils import get_normalized_group_id
    
    data_dir = Path(DATA_DIRECTORY)
    processing_dir = Path(UPLOAD_PROCESSING_DIRECTORY)
    
    if not data_dir.exists():
        return []
    
    # Нормализуем group_id для поиска в data/ (всегда 5 цифр)
    normalized_id = get_normalized_group_id(group_id)
    
    # Находим все файлы по нормализованному имени в data/
    all_files_in_data = list(data_dir.glob(f"{normalized_id}.*"))
    
    if not all_files_in_data:
        return []
    
    # Находим все файлы с началом group_id в processing/ (оригинальное имя)
    files_in_processing = list(processing_dir.glob(f"{group_id}.*"))
    
    if not files_in_processing:
        return []
    
    # Получаем расширения файлов в processing/
    processing_extensions = {f.suffix.lower() for f in files_in_processing}

    # Расширяем до классов расширений: если новая группа несёт любой архив
    # (.zip или .pdf), бэкапим ВСЕ архивы из data/ независимо от расширения.
    # Это предотвращает появление «осиротевших» архивов другого типа в data/.
    _ARCHIVE_EXTS = {".zip", ".pdf"}
    expanded_extensions = set(processing_extensions)
    if processing_extensions & _ARCHIVE_EXTS:
        expanded_extensions |= _ARCHIVE_EXTS

    conflicts = [
        f for f in all_files_in_data
        if f.suffix.lower() in expanded_extensions
    ]
    
    if conflicts:
        app_logger.debug(
            f"[FILE_WATCHER] Конфликты для {group_id} → {normalized_id}: "
            f"найдено {len(all_files_in_data)} файлов, "
            f"конфликтов {len(conflicts)}: {[c.name for c in conflicts]}"
        )
    
    return conflicts


def backup_to_old(files: List[Path]) -> bool:
    """
    Перемещает файлы из data/ в data.old/ с timestamp.
    
    Имя: original_name_YYYYMMDD_HHMMSSmmm.ext
    
    Args:
        files: Список файлов для бэкапа
        
    Returns:
        True если успешно
    """
    from config import BACKUP_DIRECTORY, BACKUP_TIMESTAMP_FORMAT
    
    if not files:
        return True
    
    old_dir = Path(BACKUP_DIRECTORY)
    old_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now(timezone.utc).strftime(BACKUP_TIMESTAMP_FORMAT)
    
    try:
        for file_path in files:
            # Формат: 12345-а_20251212_143052384.json
            stem = file_path.stem
            ext = file_path.suffix
            backup_name = f"{stem}_{timestamp}{ext}"
            backup_path = old_dir / backup_name
            
            # Перемещаем в old/ (используем shutil.move для cross-device совместимости)
            shutil.move(str(file_path), str(backup_path))
            
            app_logger.info(
                f"[FILE_WATCHER] Бэкап: {file_path.name} → data.old/{backup_name}"
            )
        
        return True
        
    except Exception as e:
        app_logger.error(
            f"[FILE_WATCHER] Ошибка создания бэкапа: {e}", 
            exc_info=True
        )
        return False


def copy_processing_to_data(group_id: str) -> Tuple[bool, str]:
    """
    Копирует файлы из data.up/30_processing/ в data/ с нормализацией имен.
    
    ВАЖНО: 
    - Валидация уже выполнена ранее (Проверка №1 и №2)
    - Имена файлов нормализуются: Шифр приводится к 5 цифрам (00012)
    - Нормализация применяется к JSON, PDF, ZIP файлам
    
    Args:
        group_id: ID группы (может быть "12-FRT" или "00012-FRT")
        
    Returns:
        (success: bool, error_msg: str)
        - success: True если успешно
        - error_msg: Пустая строка при успехе или детальное описание ошибки
    """
    from config import UPLOAD_PROCESSING_DIRECTORY, DATA_DIRECTORY
    from .utils import normalize_filename_for_data
    
    processing_dir = Path(UPLOAD_PROCESSING_DIRECTORY)
    data_dir = Path(DATA_DIRECTORY)
    data_dir.mkdir(parents=True, exist_ok=True)
    
    # Находим файлы группы в processing/
    group_files = list(processing_dir.glob(f"{group_id}.*"))
    
    if not group_files:
        error_msg = f"Файлы группы {group_id} не найдены в processing/"
        app_logger.warning(f"[FILE_WATCHER] {error_msg}")
        return False, error_msg
    
    try:
        # Копируем все файлы группы с нормализацией имен
        for file_path in group_files:
            # Нормализуем имя файла: Шифр → 5 цифр
            normalized_name = normalize_filename_for_data(file_path.name)
            target_path = data_dir / normalized_name
            
            shutil.copy2(file_path, target_path)
            
            # Логируем нормализацию, если имя изменилось
            if file_path.name != normalized_name:
                app_logger.debug(
                    f"[FILE_WATCHER] Скопирован с нормализацией: "
                    f"{file_path.name} → data/{normalized_name}"
                )
            else:
                app_logger.debug(f"[FILE_WATCHER] Скопирован: {file_path.name} → data/")
        
        app_logger.info(
            f"[FILE_WATCHER] Группа {group_id} скопирована в data/ "
            f"({len(group_files)} файлов)"
        )
        return True, ""
        
    except Exception as e:
        error_msg = f"Ошибка копирования: {str(e)}\n{traceback.format_exc()}"
        app_logger.error(
            f"[FILE_WATCHER] Ошибка копирования группы {group_id}: {e}", 
            exc_info=True
        )
        return False, error_msg


def process_partial_group(group_id: str, files: List[Path]) -> Tuple[bool, str]:
    """
    Обрабатывает частичную группу (PDF/ZIP без JSON).
    Копирует только PDF/ZIP файлы в data/ с нормализацией имен.
    НЕ перестраивает БД, НЕ валидирует JSON.
    
    Args:
        group_id: ID группы (может быть "12-FRT" или "00012-FRT")
        files: Список файлов (только PDF/ZIP, без JSON)
        
    Returns:
        (success: bool, error_msg: str)
        - success: True если успешно
        - error_msg: Пустая строка при успехе или детальное описание ошибки
    """
    from config import UPLOAD_PROCESSING_DIRECTORY, DATA_DIRECTORY
    from .utils import normalize_filename_for_data
    
    processing_dir = Path(UPLOAD_PROCESSING_DIRECTORY)
    data_dir = Path(DATA_DIRECTORY)
    data_dir.mkdir(parents=True, exist_ok=True)
    
    # Находим файлы группы в processing/
    group_files = list(processing_dir.glob(f"{group_id}.*"))
    
    # Исключаем JSON файлы (их быть не должно, но на всякий случай)
    non_json_files = [f for f in group_files if f.suffix.lower() != '.json']
    
    if not non_json_files:
        error_msg = f"Нет файлов для копирования в группе {group_id}"
        app_logger.warning(f"[FILE_WATCHER] {error_msg}")
        return False, error_msg
    
    try:
        # Копируем файлы с нормализацией имен (PDF/ZIP)
        for file_path in non_json_files:
            # Нормализуем имя файла: Шифр → 5 цифр
            normalized_name = normalize_filename_for_data(file_path.name)
            target_path = data_dir / normalized_name
            
            shutil.copy2(file_path, target_path)
            
            # Логируем нормализацию, если имя изменилось
            if file_path.name != normalized_name:
                app_logger.debug(
                    f"[FILE_WATCHER] Скопирован (частично) с нормализацией: "
                    f"{file_path.name} → data/{normalized_name}"
                )
            else:
                app_logger.debug(f"[FILE_WATCHER] Скопирован (частично): {file_path.name} → data/")
        
        app_logger.info(
            f"[FILE_WATCHER] Частичная группа {group_id} скопирована в data/ "
            f"({len(non_json_files)} файлов, БД не перестраивается)"
        )
        return True, ""
        
    except Exception as e:
        error_msg = f"Ошибка копирования: {str(e)}\n{traceback.format_exc()}"
        app_logger.error(
            f"[FILE_WATCHER] Ошибка копирования частичной группы {group_id}: {e}", 
            exc_info=True
        )
        return False, error_msg


def has_json_file(group_id: str) -> bool:
    """
    Проверяет наличие JSON файла в processing/ для группы.
    
    Args:
        group_id: ID группы
        
    Returns:
        True если JSON файл существует
    """
    from config import UPLOAD_PROCESSING_DIRECTORY
    processing_dir = Path(UPLOAD_PROCESSING_DIRECTORY)
    return (processing_dir / f"{group_id}.json").exists()


def canonicalize_json_dopshifr(group_id: str) -> bool:
    """
    Приводит поле ДопШифр внутри JSON-файла в 30_processing/ к UPPERCASE.

    Нужно для случаев, когда оператор кладёт файл с "ДопШифр": "tst" вручную:
    имя файла нормализуется раньше (00001-TST.json), а поле внутри остаётся
    в нижнем регистре и попадает в БД и справочник именно так.

    Вызывается после validate_json_in_processing, перед финальной проверкой БД.
    Файл не перезаписывается, если значение уже в верхнем регистре или пустое.

    Args:
        group_id: нормализованный ID группы (например, "00001-TST")

    Returns:
        True  — успешно (включая no-op)
        False — ошибка чтения или записи (группу следует отправить в 40_error/)
    """
    import json
    from config import UPLOAD_PROCESSING_DIRECTORY
    from services.json_io import read_json
    from services.id_utils import normalize_dopshifr

    json_path = Path(UPLOAD_PROCESSING_DIRECTORY) / f"{group_id}.json"
    try:
        data = read_json(json_path)
    except Exception as e:
        app_logger.error(
            f"[FILE_WATCHER] canonicalize_json_dopshifr: ошибка чтения {json_path.name}: {e}",
            exc_info=True,
        )
        return False

    raw = data.get("ДопШифр")
    if not raw:
        return True

    canonical = normalize_dopshifr(raw)
    if canonical == raw:
        return True

    data["ДопШифр"] = canonical
    try:
        json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        app_logger.info(
            f"[FILE_WATCHER] ДопШифр канонизирован: {group_id}: '{raw}' → '{canonical}'"
        )
    except Exception as e:
        app_logger.error(
            f"[FILE_WATCHER] canonicalize_json_dopshifr: ошибка записи {json_path.name}: {e}",
            exc_info=True,
        )
        return False

    return True
