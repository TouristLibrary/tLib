# Version 1.3 - 14.05.2026 00:00:00 GMT
# File Watcher Task - Фоновая задача обработки загруженных файлов
# Описание: Асинхронная фоновая задача для автоматической обработки загружаемых файлов через staged pipeline.
#           Pipeline: data.up/20_go/ → 30_processing/ → done/ (успех) или 40_error/ (ошибка).
#           Приостановка: создайте директорию data.up/pause для временной остановки обработки.
#           Этапы обработки: 1) Сканирование data.up/20_go/, 2) Перемещение в 30_processing/,
#           3) Бэкап конфликтующих файлов, 4) Копирование в data/, 5) Генерация БД,
#           6) Перемещение в done/40_error, 7) Публикация БД, 8) Очистка done/.
#           Работает в бесконечном цикле с интервалом проверки из конфигурации.
#           Обработка ошибок: все исключения логируются, задача продолжает работать.

import asyncio
import logging
from fastapi import FastAPI

# Импорт unified logging system
from logging_config import app_logger, log_with_data

# Импорт функций file_watcher (явно, без зависимости от package init)
from .utils import initialize_directories
from .pipeline import process_upload_cycle
from .notify import process_pending_notifications

# Импорт конфигурационных констант
from config import (
    FILE_WATCHER_CHECK_INTERVAL,
    UPLOAD_GO_DIRECTORY,
)


async def file_watcher_task(app_instance: FastAPI):
    """
    Фоновая задача для обработки загруженных файлов через staged pipeline.
    
    Pipeline: data.up/20_go/ → 30_processing/ → done/ или 40_error/
    
    Приостановка: создайте директорию data.up/pause для временной остановки обработки.
    
    Этапы:
    1. Сканирует data.up/20_go/ каждые N секунд
    2. Перемещает группы файлов в 30_processing/
    3. Делает бэкап конфликтующих файлов
    4. Копирует в data/
    5. Генерирует БД
    6. Перемещает в done/ (успех) или 40_error/ (ошибка)
    7. Публикует БД если есть успешные
    8. Очищает done/ (auto-delete)
    """
    log_with_data(logging.INFO, "File watcher запущен",
                 interval=FILE_WATCHER_CHECK_INTERVAL,
                 source=UPLOAD_GO_DIRECTORY)
    
    if not initialize_directories():
        app_logger.critical("Не удалось инициализировать директории File Watcher")
        return
    
    while True:
        try:
            async with app_instance.state.db_lock:
                stats = await asyncio.to_thread(
                    process_upload_cycle, app_state=app_instance.state
                )
            
            # Логируем только если были файлы
            if stats["scanned"] > 0:
                app_logger.info(
                    f"[FILE_WATCHER] Цикл завершен: "
                    f"найдено={stats['scanned']}, "
                    f"групп={stats['groups']}, "
                    f"в обработке={stats['moved_to_processing']}, "
                    f"успешно={stats['success']}, "
                    f"ошибок={stats['errors']}, "
                    f"БД обновлена={stats['db_updated']}"
                )
                
        except Exception as e:
            app_logger.error(
                f"[FILE_WATCHER] Критическая ошибка в цикле обработки: {e}", 
                exc_info=True
            )

        # Сверка маркеров уведомлений — вне db_lock (SMTP может быть медленным)
        try:
            await asyncio.to_thread(process_pending_notifications)
        except Exception as e:
            app_logger.error(f"[FILE_WATCHER] Ошибка сверки уведомлений: {e}", exc_info=True)

        await asyncio.sleep(FILE_WATCHER_CHECK_INTERVAL)
