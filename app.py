# Version 6.8 - 03.09.2026 13:26:00 GMT
# FastAPI приложение для просмотра архивов и поиска в базе данных
# Описание: Главный файл приложения, инициализирует FastAPI сервер, регистрирует роутеры и middleware.
# 6.8: удалён signal_handler (перехватывал SIGTERM/SIGINT и только логировал их, не завершая
#           процесс) — из-за него uvicorn не получал сигнал, graceful shutdown (lifespan) не
#           отрабатывал никогда, и остановка/рестарт сервиса всегда заканчивались SIGKILL по
#           TimeoutStopSec. Убран и неиспользуемый после этого import signal.
# 6.7: app.state.hidden_reports — кэш списка скрытых отчётов (см. services/hidden_reports.py),
#           загружается после init_auth_db() (настройка хранится в auth.db).
# 6.6: удалён неиспользуемый импорт DIGEST_TIMEZONE_OFFSET_HOURS (используется только в digest.py).
#           Обслуживает статические файлы (JS, CSS, assets, data).
#           При запуске загружает справочные данные и количество отчетов из БД, таблицу редиректов из поля СтарыйID,
#           а также сохраняет reference_version в app.state для проверки актуальности справочников на фронтенде.
#           Запускает фоновые задачи: автообновление БД (tlib-new.db) и File Watcher (обработка загружаемых файлов).
#           Архитектура: модульная структура с роутерами (archive, search, lists, redirect, static),
#           middleware (path_security, rate_limit, security_headers, http_methods, request_id) и сервисами (file, validation, database).
#           Безопасность: детекция Path Traversal атак, валидация путей, ограничения размеров, rate limiting,
#           security headers, фильтрация HTTP методов. Все попытки атак логируются в critical.log.
#           Резервные копии: все бэкапы (файлов и БД) сохраняются в data.old/ с ручным удалением.
#           Платформа: Ubuntu Server (production).

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv("data.secret/.env")

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import uvicorn

# Импорт unified logging system
from logging_config import (
    app_logger,
    log_with_data
)

# Импорт middleware из модульной структуры
from middlewares.rate_limit import RateLimitMiddleware
from middlewares.security_headers import SecurityHeadersMiddleware
from middlewares.http_methods import HTTPMethodFilterMiddleware
from middlewares.path_security import PathSecurityMiddleware
from middlewares.request_id import RequestIDMiddleware
from middlewares.data_download import DataDownloadMiddleware
from middlewares.traffic_stats import TrafficStatsMiddleware, StatsCollector

# Импорт бизнес-логики из services
from services.database import (
    load_reference_lists,
    load_redirect_table
)
from services.database.search_limiter import HeavyQueryLimiter
from services.database.database_watcher_task import database_watcher_task
from services.file_watcher.file_watcher_task import file_watcher_task
from services.alerts.digest import daily_digest_task
from services.alerts.alerter import send_admin_alert_direct

# Импорт конфигурации полей
from config import get_default_reference_values, STATE_KATEGORIA_UNIFIED, STATE_REPORTS_COUNT

# Импорт конфигурационных констант
from config import (
    DEFAULT_PORT, DEFAULT_HOST,
    STATIC_DIRS, DATABASE_PATH, LOCAL_ARCHIVE_PATH, DATA_DIRECTORY, CACHE_DIRECTORY, CACHE_URL_PATH,
    RATE_LIMIT_REQUESTS_PER_MINUTE,
    MAX_CONCURRENT_HEAVY_QUERIES, HEAVY_QUERY_QUEUE_WARNING_SIZE,
    APP_TITLE, APP_DESCRIPTION, APP_VERSION,
    STATE_DB_WATCHER_TASK, STATE_FILE_WATCHER_TASK,
    STATE_STATS_FLUSH_TASK,
    STATS_DB_PATH, STATS_FLUSH_INTERVAL, STATS_RETENTION_DAYS,
)

# Импорт роутеров
from routers import archive_router, pdf_router, search_router, lists_router, static_router, health_router, config_router, cache_router, png_viewer_router, admin_router, auth_router, upload_router, sitemap_router

# Импорт auth DB
from services.auth.auth_db import init_auth_db, cleanup_expired
from config import AUTH_CLEANUP_INTERVAL

# Импорт сервиса скрытых отчётов
from services.hidden_reports import load_hidden_reports

# ============================================================================
# ЛОГИРОВАНИЕ (настроено в logging_config.py)
# ============================================================================

app_logger.info("Запуск TlibWebApp")


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

async def _stats_flush_loop(app) -> None:
    """Периодически сбрасывает in-memory статистику посещений в stats.db."""
    while True:
        await asyncio.sleep(STATS_FLUSH_INTERVAL)
        try:
            await app.state.stats_collector.flush()
        except Exception as e:
            app_logger.error(f"[stats_flush_loop] Ошибка: {e}")


async def _auth_cleanup_loop() -> None:
    """Удаляет просроченные magic links и сессии из auth.db раз в час."""
    while True:
        await asyncio.sleep(AUTH_CLEANUP_INTERVAL)
        try:
            cleanup_expired()
        except Exception as e:
            app_logger.error(f"[auth_cleanup] Ошибка: {e}")


# ============================================================================
# LIFESPAN
# ============================================================================

@asynccontextmanager
async def lifespan(app):
    # --- startup ---
    # Загружаем справочные данные из базы данных
    try:
        reference_lists = load_reference_lists(DATABASE_PATH)
        
        # Сохраняем в app.state для доступа из роутеров
        app.state.dopshifr_list = reference_lists['dopshifr_list']
        app.state.raion_obshiy_list = reference_lists['raion_obshiy_list']
        app.state.tip_list = reference_lists['tip_list']
        app.state.kategoria_s_list = reference_lists['kategoria_s_list']
        app.state.kategoria_po_list = reference_lists['kategoria_po_list']
        app.state.kategoria_unified_list = reference_lists[STATE_KATEGORIA_UNIFIED]
        app.state.reports_count = reference_lists[STATE_REPORTS_COUNT]
        app.state.reference_version = datetime.now(timezone.utc).isoformat()
        app.state.started_at = datetime.now(timezone.utc).isoformat()
        
        log_with_data(logging.INFO, "Справочные данные загружены",
                     reports=reference_lists[STATE_REPORTS_COUNT])
        
    except Exception as e:
        app_logger.error(f"Ошибка при инициализации справочных данных: {e}", exc_info=True)
        # Устанавливаем значения по умолчанию при критической ошибке
        defaults = get_default_reference_values()
        for key, value in defaults.items():
            setattr(app.state, key, value)
        app.state.reference_version = datetime.now(timezone.utc).isoformat()
        app.state.started_at = datetime.now(timezone.utc).isoformat()
    
    # Загружаем таблицу редиректов из БД (поле СтарыйID)
    app.state.redirect_table = load_redirect_table(DATABASE_PATH)
    
    # Инициализируем лимитер тяжёлых запросов
    app.state.heavy_query_limiter = HeavyQueryLimiter(
        MAX_CONCURRENT_HEAVY_QUERIES,
        HEAVY_QUERY_QUEUE_WARNING_SIZE
    )
    log_with_data(logging.INFO, "Heavy query limiter initialized",
                 max_concurrent=MAX_CONCURRENT_HEAVY_QUERIES,
                 queue_warning_size=HEAVY_QUERY_QUEUE_WARNING_SIZE)
    
    # Общий asyncio.Lock для сериализации операций записи в БД между watcher-задачами.
    # Захватывается перед запуском process_upload_cycle и perform_database_update,
    # чтобы они не выполнялись одновременно из разных потоков (to_thread).
    app.state.db_lock = asyncio.Lock()

    # Запускаем фоновую задачу автообновления БД
    app.state.db_watcher_task = asyncio.create_task(database_watcher_task(app))
    
    # Запускаем File Watcher (staged pipeline)
    app.state.file_watcher_task = asyncio.create_task(file_watcher_task(app))

    # Инициализируем сборщик статистики посещений и запускаем периодический flush
    app.state.stats_collector = StatsCollector(STATS_DB_PATH, STATS_RETENTION_DAYS)
    app.state.stats_flush_task = asyncio.create_task(_stats_flush_loop(app))
    log_with_data(logging.INFO, "Stats collector initialized", db=STATS_DB_PATH)

    # Инициализируем БД авторизации и запускаем фоновую очистку
    init_auth_db()
    app.state.auth_cleanup_task = asyncio.create_task(_auth_cleanup_loop())
    app_logger.info("Auth DB initialized")

    # Загружаем список скрытых отчётов (настройка в auth.db, см. services/hidden_reports.py)
    app.state.hidden_reports = load_hidden_reports()
    log_with_data(logging.INFO, "Скрытые отчёты загружены", count=len(app.state.hidden_reports))

    # Запускаем задачу ежедневного дайджеста и проверки диска
    app.state.digest_task = asyncio.create_task(daily_digest_task(app))

    port = os.environ.get('PORT', str(DEFAULT_PORT))
    log_with_data(logging.INFO, "Сервер запущен",
                 port=port,
                 host=DEFAULT_HOST)

    # Уведомление о старте сервера
    try:
        reports_count = getattr(app.state, 'reports_count', '?')
        started_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        subject = "[tLib] Сервер запущен"
        body = (
            f"Сервер tLib запущен (или перезапущен).\n\n"
            f"Время: {started_at}\n"
            f"Отчётов в базе: {reports_count}\n"
            f"Порт: {port}\n\n"
            f"Если рестарт не был запланирован — проверьте причину в системных логах."
        )
        send_admin_alert_direct(subject, body)
    except Exception as _e:
        app_logger.warning(f"[startup] Не удалось отправить уведомление о старте: {_e}")

    yield

    # --- shutdown ---
    # Останавливаем flush-задачу статистики и делаем финальный сброс
    if hasattr(app.state, STATE_STATS_FLUSH_TASK):
        app.state.stats_flush_task.cancel()
        try:
            await app.state.stats_flush_task
        except asyncio.CancelledError:
            pass
    if hasattr(app.state, "stats_collector"):
        await app.state.stats_collector.flush()

    # Останавливаем фоновую задачу автообновления БД
    if hasattr(app.state, STATE_DB_WATCHER_TASK):
        app.state.db_watcher_task.cancel()
        try:
            await app.state.db_watcher_task
        except asyncio.CancelledError:
            pass
    
    # Останавливаем File Watcher
    if hasattr(app.state, STATE_FILE_WATCHER_TASK):
        app.state.file_watcher_task.cancel()
        try:
            await app.state.file_watcher_task
        except asyncio.CancelledError:
            pass

    # Останавливаем фоновую очистку auth
    if hasattr(app.state, "auth_cleanup_task"):
        app.state.auth_cleanup_task.cancel()
        try:
            await app.state.auth_cleanup_task
        except asyncio.CancelledError:
            pass

    # Останавливаем задачу дайджеста
    if hasattr(app.state, "digest_task"):
        app.state.digest_task.cancel()
        try:
            await app.state.digest_task
        except asyncio.CancelledError:
            pass

    app_logger.info("Сервер остановлен")


# Создаем FastAPI приложение
app = FastAPI(
    title=APP_TITLE,
    description=APP_DESCRIPTION,
    version=APP_VERSION,
    docs_url=None,      # Отключаем /docs
    redoc_url=None,     # Отключаем /redoc
    openapi_url=None,   # Отключаем /openapi.json
    lifespan=lifespan,
)

# Регистрируем роутеры
app.include_router(health_router.router)
app.include_router(admin_router.router)
app.include_router(config_router.router)
app.include_router(cache_router.router)
app.include_router(png_viewer_router.router)
app.include_router(auth_router.router)
app.include_router(upload_router.router)
app.include_router(archive_router.router)
app.include_router(pdf_router.router)
app.include_router(search_router.router)
app.include_router(lists_router.router)
app.include_router(sitemap_router.router)
app.include_router(static_router.router)

# Регистрируем middleware
# ВАЖНО: PathSecurityMiddleware первым - перехватывает запросы до нормализации FastAPI
app.add_middleware(RequestIDMiddleware)
app.add_middleware(PathSecurityMiddleware)
app.add_middleware(HTTPMethodFilterMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(DataDownloadMiddleware)

# Добавляем Rate Limiting из конфигурации
app.add_middleware(RateLimitMiddleware, requests_per_minute=RATE_LIMIT_REQUESTS_PER_MINUTE)
app.add_middleware(TrafficStatsMiddleware)

# Монтируем статические директории из конфигурации
# Проверяем существование директорий перед монтированием
for static_dir in STATIC_DIRS:
    if Path(static_dir).exists():
        app.mount(f"/{static_dir}", StaticFiles(directory=static_dir), name=static_dir)

# Универсальное монтирование data директории
# Поддерживает абсолютные пути (USB) и относительные пути (локальные)
# URL путь всегда /data, физический путь - из DATA_DIRECTORY
data_mount_path = LOCAL_ARCHIVE_PATH  # "/data" из config.py
if Path(DATA_DIRECTORY).exists():
    app.mount(data_mount_path, StaticFiles(directory=DATA_DIRECTORY), name="data")
    app_logger.info(f"Data directory mounted: {DATA_DIRECTORY} -> {data_mount_path}")
else:
    app_logger.warning(f"Data directory not found: {DATA_DIRECTORY}")

# Монтируем cache директорию для PNG viewer
if Path(CACHE_DIRECTORY).exists():
    app.mount(CACHE_URL_PATH, StaticFiles(directory=CACHE_DIRECTORY), name="cache")
    app_logger.info(f"Cache directory mounted: {CACHE_DIRECTORY} -> {CACHE_URL_PATH}")
else:
    app_logger.warning(f"Cache directory not found: {CACHE_DIRECTORY}")

# Обработка SIGTERM/SIGINT для graceful shutdown не переопределяется — её обеспечивает
# сам uvicorn, вызывая shutdown-ветку lifespan (см. выше). Собственный signal_handler
# был удалён в 6.8: он только логировал сигнал и не завершал процесс.

# Запуск приложения
if __name__ == "__main__":
    port = int(os.environ.get("PORT", str(DEFAULT_PORT)))
    uvicorn.run(app, host=DEFAULT_HOST, port=port) 