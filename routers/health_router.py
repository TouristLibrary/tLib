# Version 1.2 - 19.06.2026 14:34:00 GMT
# Health Check Router для TlibWebApp
# Описание: Endpoint для мониторинга состояния приложения. Используется Docker, Kubernetes, load balancer'ами
#           для проверки работоспособности сервиса. Выполняет проверку доступности базы данных (SQLite),
#           состояния фоновых задач (database_watcher_task, file_watcher_task).
#           Возвращает три статуса: healthy (все работает), degraded (БД работает, задачи упали),
#           unhealthy (БД недоступна). HTTP статус 503 при unhealthy для корректной работы load balancer.
# 1.1: подключение к БД переведено на open_tlib_db() (read-only).
# 1.2: /health не раскрывает путь к БД и детали ошибок (общие сообщения; детали — в app_logger).

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request, Response

# Импорт логгеров
from logging_config import app_logger

# Импорт конфигурационных констант
from config import DATABASE_PATH, STATE_DB_WATCHER_TASK, STATE_FILE_WATCHER_TASK
from services.database import open_tlib_db

# Создаем роутер
router = APIRouter(tags=["health"])


def _check_background_task(request, state_key: str, task_attr: str) -> dict:
    """Проверяет состояние фоновой задачи, возвращает dict для checks."""
    try:
        if hasattr(request.app.state, state_key):
            task = getattr(request.app.state, task_attr)
            is_running = not task.done()
            if is_running:
                return {"status": "ok", "running": True}
            else:
                app_logger.warning(f"Health check: {task_attr} is not running")
                return {"status": "error", "running": False, "message": "Task has stopped"}
        else:
            return {"status": "error", "running": False, "message": "Task not initialized"}
    except Exception as e:
        app_logger.error(f"Health check: error checking {task_attr} - {e}")
        return {"status": "error", "message": "Task check failed"}


@router.get("/health")
async def health_check(request: Request, response: Response):
    """
    Health check endpoint для мониторинга состояния приложения.
    
    Проверяет:
    1. Доступность базы данных (SQLite connect + SELECT 1)
    2. Состояние фоновой задачи database_watcher_task
    3. Состояние фоновой задачи file_watcher_task
    
    Возвращает:
    - healthy (200): БД доступна, обе задачи работают
    - degraded (200): БД доступна, но одна или обе задачи упали
    - unhealthy (503): БД недоступна (критическая ошибка)
    
    Returns:
        JSON с общим статусом и деталями по каждой проверке
    """
    checks = {}
    overall_status = "healthy"
    
    # ============================================================================
    # 1. Проверка базы данных
    # ============================================================================
    db_ok = False
    try:
        # Проверяем существование файла
        if not Path(DATABASE_PATH).exists():
            checks["database"] = {
                "status": "error",
                "message": "Database file not found"
            }
        else:
            # Проверяем подключение и выполняем простой запрос
            conn = open_tlib_db(row_factory=False)
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            conn.close()

            checks["database"] = {"status": "ok"}
            db_ok = True

    except sqlite3.Error as e:
        checks["database"] = {
            "status": "error",
            "message": "Database error"
        }
        app_logger.error(f"Health check: database error - {e}")
    except Exception as e:
        checks["database"] = {
            "status": "error",
            "message": "Database check failed"
        }
        app_logger.error(f"Health check: unexpected database error - {e}")
    
    # ============================================================================
    # 2. Проверка фоновых задач
    # ============================================================================

    db_check = _check_background_task(request, STATE_DB_WATCHER_TASK, "db_watcher_task")
    checks["db_watcher"] = db_check
    if db_check["status"] == "error":
        overall_status = "degraded"

    fw_check = _check_background_task(request, STATE_FILE_WATCHER_TASK, "file_watcher_task")
    checks["file_watcher"] = fw_check
    if fw_check["status"] == "error":
        overall_status = "degraded"
    
    # ============================================================================
    # 3. Определение финального статуса
    # ============================================================================
    
    # Если БД недоступна - это критическая ошибка
    if not db_ok:
        overall_status = "unhealthy"
        response.status_code = 503  # Service Unavailable
    else:
        response.status_code = 200
    
    # ============================================================================
    # 4. Формирование ответа
    # ============================================================================
    
    return {
        "status": overall_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks
    }
