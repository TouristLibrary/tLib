# Version 2.0 - 14.05.2026 00:00:00 GMT
# File Watcher Deleter - Перенос файлов группы из data/ в data.old/
# Описание: Обработка .delete триггеров для удаления файлов отчёта из data/.
#           process_delete_operation() переносит файлы группы из data/ в data.old/ с timestamp
#           и инвалидирует кеш архива. Запись в БД НЕ меняется здесь — пересборка tlib-new.db
#           из обновлённой data/ выполняется в pipeline._process_delete_operations через
#           единый канал: generate_final_database_check([]) → publish_database() →
#           database_watcher_task → perform_database_update (refresh app.state).
#           Все операции логируются в logs/app.log.

import traceback
from pathlib import Path
from typing import Tuple

from logging_config import app_logger
from config import DATA_DIRECTORY
from services.cache.cache_service import invalidate_archive_cache
from .file_operations import backup_to_old
from .utils import get_normalized_group_id


def process_delete_operation(group_id: str, shifr: str, dopshifr: str = None) -> Tuple[bool, int, str]:
    """
    Переносит файлы группы из data/ в data.old/ и инвалидирует кеш архива.

    Алгоритм:
    1. Поиск файлов группы в data/ по нормализованному ID
    2. Бэкап файлов в data.old/ с timestamp
    3. Инвалидация кеша архива

    Пересборка tlib-new.db НЕ выполняется здесь — это делает вызывающий пайплайн
    после завершения всего батча DELETE-операций.

    Args:
        group_id: ID группы (например "00012-FRT" или "12345")
        shifr: Шифр (строка с цифрами) — используется только для логирования
        dopshifr: ДопШифр или None — используется только для логирования

    Returns:
        (success: bool, files_moved: int, error_msg: str)
        - success: True если операция завершена без ошибок
                   (включая случай, когда файлов в data/ не оказалось — идемпотентный no-op)
        - files_moved: количество фактически перенесённых файлов (0 = no-op, пересборка не нужна)
        - error_msg: пустая строка при успехе или детальное описание ошибки
    """
    try:
        data_dir = Path(DATA_DIRECTORY)

        # Нормализуем group_id для поиска в data/ (всегда 5 цифр)
        normalized_id = get_normalized_group_id(group_id)
        files_to_backup = list(data_dir.glob(f"{normalized_id}.*"))

        if not files_to_backup:
            # Файлов нет — идемпотентный no-op (повторный триггер или уже удалено)
            app_logger.warning(
                f"[FILE_WATCHER] DELETE {group_id} → {normalized_id}: "
                f"файлы не найдены в data/, пропускаем (no-op)"
            )
            return True, 0, ""

        app_logger.info(
            f"[FILE_WATCHER] DELETE {group_id} → {normalized_id}: "
            f"найдено {len(files_to_backup)} файлов для бэкапа"
        )

        if not backup_to_old(files_to_backup):
            return False, 0, "Ошибка создания бэкапа файлов"

        files_moved = len(files_to_backup)
        app_logger.info(
            f"[FILE_WATCHER] DELETE {group_id}: "
            f"файлы перенесены в data.old/ ({files_moved} шт.)"
        )

        # Инвалидация кеша архива
        deleted_cache = invalidate_archive_cache(normalized_id)
        if deleted_cache > 0:
            app_logger.info(
                f"[FILE_WATCHER] DELETE {group_id} → {normalized_id}: "
                f"кеш удалён ({deleted_cache} файлов)"
            )

        return True, files_moved, ""

    except Exception as e:
        error_msg = f"Критическая ошибка DELETE: {str(e)}\n{traceback.format_exc()}"
        app_logger.error(
            f"[FILE_WATCHER] DELETE {group_id}: {error_msg}",
            exc_info=True
        )
        return False, 0, error_msg
