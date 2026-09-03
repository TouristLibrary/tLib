# Version 1.2 - 12.06.2026 14:00:00 GMT
# Database Watcher Task - Фоновая задача автообновления БД
# Описание: Асинхронная фоновая задача для автоматического обновления базы данных.
#           Периодически проверяет наличие файла-триггера (tlib-new.db) в директории assets/.
#           При обнаружении файла выполняет: валидацию SQLite базы, создание бэкапа текущей БД,
#           атомарную замену базы данных, обновление кэша в app.state, удаление старых бэкапов.
#           Работает в бесконечном цикле с интервалом проверки из конфигурации.
#           Все операции выполняются через update_service.perform_database_update().
#           Обработка ошибок: все исключения логируются, задача продолжает работать.

import asyncio
import logging
from pathlib import Path
from fastapi import FastAPI

# Импорт unified logging system
from logging_config import app_logger, log_with_data

# Импорт бизнес-логики обновления БД
from .update_service import perform_database_update

# Импорт алертов (ленивый — не критично при старте)
try:
    from services.alerts.alerter import send_admin_alert as _send_alert
except Exception:
    _send_alert = None

# Импорт конфигурационных констант
from config import (
    DATABASE_PATH,
    DATABASE_NEW_FILE,
    DATABASE_BACKUP_PATTERN,
    DATABASE_CHECK_INTERVAL
)


async def database_watcher_task(app_instance: FastAPI):
    """
    Фоновая задача для автоматического обновления базы данных.
    
    Периодически проверяет наличие файла-триггера (tlib-new.db) в директории assets/.
    При обнаружении файла:
    1. Валидирует его как SQLite базу
    2. Создаёт бэкап текущей базы
    3. Атомарно заменяет базу данных
    4. Обновляет кэш в app.state
    5. Удаляет старые бэкапы
    
    Args:
        app_instance: Экземпляр FastAPI приложения для доступа к app.state
    """
    db_dir = Path(DATABASE_PATH).parent
    
    log_with_data(logging.INFO, "БД watcher запущен",
                 interval=DATABASE_CHECK_INTERVAL,
                 trigger=DATABASE_NEW_FILE)
    
    while True:
        try:
            async with app_instance.state.db_lock:
                await asyncio.to_thread(
                    perform_database_update,
                    db_dir=db_dir,
                    app_state=app_instance.state,
                    db_path=DATABASE_PATH,
                    backup_pattern=DATABASE_BACKUP_PATTERN,
                    retention_days=0,  # не используется
                    new_file_name=DATABASE_NEW_FILE,
                )
        except Exception as e:
            app_logger.error(f"Ошибка в фоновой задаче автообновления БД: {e}", exc_info=True)
            if _send_alert:
                try:
                    _send_alert(
                        "DB_SWAP_FAILED",
                        error_type=type(e).__name__,
                        error=str(e)[:200],
                    )
                except Exception:
                    pass
        
        # Ожидаем перед следующей проверкой
        await asyncio.sleep(DATABASE_CHECK_INTERVAL)
