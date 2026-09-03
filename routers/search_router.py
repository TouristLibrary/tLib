# Version 3.7 - 10.07.2026 09:45:00 GMT
# Search Router для TlibWebApp с поддержкой пагинации и ограничением тяжёлых запросов
# Описание: API endpoint POST /api/search для серверного поиска в базе данных SQLite. Принимает параметры формы поиска,
#           поддерживает все поля: Шифр, ДопШифр, Маршрут, Район, Автор, РайонОбщий, Тип, КатегорияС, КатегорияПо, Год (через ГодС/ГодПо в форме), МесяцС, МесяцПо.
#           Выполняет регистронезависимый поиск с поддержкой кириллицы через пользовательскую функцию LOWER.
#           Регистрирует функцию CATEGORY_INDEX для определения индекса категории по сложности в упорядоченном списке.
#           Поиск по категориям находит максимальную категорию похода и проверяет её вхождение в диапазон поиска.
#           Поддерживает пагинацию через параметры limit и offset: при их наличии возвращает страницу данных с метаданными (total, has_more).
#           Без параметров пагинации работает в обратно совместимом режиме (возвращает все результаты).
#           Ограничение тяжёлых запросов: запросы без селективных фильтров проверяются через COUNT, при превышении порога
#           ставятся в очередь через HeavyQueryLimiter для ограничения параллельного выполнения.
#           Использует параметризованные запросы для защиты от SQL Injection, логирует каждый этап обработки запроса.
#           Использует StreamingResponse для корректной обработки больших JSON с кириллицей (ensure_ascii=False).
#           SQLite-операции выполняются через asyncio.to_thread() — event loop не блокируется во время запросов к БД.
# 3.6: SQL-исполнение (count_search/execute_search) вынесено в services/database/search_executor.py.
# 3.7: _annotate_hidden — проставляет row["Скрыт"] по app.state.hidden_reports (отчёт остаётся
#           в поиске, но фронтенд скрывает файл; см. services/hidden_reports.py).

import asyncio
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pathlib import Path
import logging
import time
import json

# Импорт services
from services.database import build_search_query, count_search, execute_search
from services.database.search_limiter import is_light_query
from services.id_utils import make_norm_id

# Импорт конфигурации
from config import (
    DATABASE_PATH,
    HEAVY_QUERY_THRESHOLD,
    FILTER_MIN_LENGTH
)

# Импорт логгеров
from logging_config import app_logger, log_with_data

# Создаем роутер
router = APIRouter(prefix="/api", tags=["search"])


def _annotate_hidden(results: list, app_state) -> None:
    """Проставляет строкам results["Скрыт"] = True для отчётов из app.state.hidden_reports.
    Отчёт участвует в поиске как обычно, но фронтенд по этому флагу скрывает файл (карточка-только)."""
    hidden = getattr(app_state, "hidden_reports", None)
    if not hidden:
        return
    for row in results:
        try:
            norm_id = make_norm_id(row.get("Шифр"), row.get("ДопШифр") or "")
        except (ValueError, TypeError):
            continue
        row["Скрыт"] = norm_id in hidden


# ============================================================================
# ENDPOINT
# ============================================================================

@router.post("/search")
async def search_database(request: Request):
    """
    API для поиска в базе данных.
    Принимает параметры поиска и возвращает результаты.

    Ограничение тяжёлых запросов:
    - Запросы с селективными фильтрами (Шифр, ДопШифр, Автор и т.д.) считаются лёгкими
    - Запросы без фильтров проверяются через COUNT
    - Если COUNT > HEAVY_QUERY_THRESHOLD — запрос ставится в очередь лимитера

    SQLite-операции выполняются в asyncio.to_thread() — event loop остаётся
    свободным для обслуживания других запросов во время работы с БД.
    """
    client_ip = request.client.host if request.client else "unknown"
    start_time = time.time()
    is_heavy_query = False
    limiter = request.app.state.heavy_query_limiter

    try:
        # Получаем данные формы
        form_data = await request.form()
        form_dict = dict(form_data)

        log_with_data(logging.INFO, "Поиск",
                     endpoint="/api/search",
                     fields=len(form_dict),
                     ip=client_ip)

        # Проверяем наличие БД
        db_path = Path(DATABASE_PATH)
        if not db_path.exists():
            log_with_data(logging.ERROR, "БД не найдена",
                         path=str(db_path))
            return {
                "success": False,
                "error": "База данных не найдена"
            }

        db_path_str = str(db_path)
        kategoria_list = request.app.state.kategoria_unified_list

        # Строим SQL запрос (pure Python, на event loop)
        query, params = build_search_query(
            form_data,
            kategoria_list=kategoria_list
        )

        # Парсим пагинацию до thread-вызовов (pure Python, на event loop)
        limit: int | None = None
        offset: int = 0
        limit_raw = form_data.get('limit')
        if limit_raw:
            try:
                limit = int(limit_raw)
                offset = int(form_data.get('offset') or 0)
            except ValueError:
                limit = None
                offset = 0

        # Определение тяжести запроса
        # Шаг 1: Эвристика — проверяем наличие селективных фильтров
        known_total: int | None = None
        if not is_light_query(form_dict, FILTER_MIN_LENGTH):
            # Шаг 2: COUNT для точного определения тяжести — в потоке
            total_count = await asyncio.to_thread(
                count_search, db_path_str, kategoria_list, query, params
            )
            known_total = total_count

            if total_count > HEAVY_QUERY_THRESHOLD:
                is_heavy_query = True
                log_with_data(logging.INFO, "Heavy query detected",
                             count=total_count,
                             threshold=HEAVY_QUERY_THRESHOLD,
                             ip=client_ip)
                # Ждём слот в очереди тяжёлых запросов
                await limiter.acquire()

        try:
            # Основной запрос — в потоке
            results, total_count = await asyncio.to_thread(
                execute_search,
                db_path_str, kategoria_list, query, params,
                limit, offset, known_total
            )

            _annotate_hidden(results, request.app.state)

            time_ms = round((time.time() - start_time) * 1000, 2)

            if limit is not None:
                # Режим пагинации
                log_with_data(logging.INFO, "Поиск завершен (страница)",
                             results=len(results),
                             total=total_count,
                             offset=offset,
                             heavy=is_heavy_query,
                             time_ms=time_ms)

                response_data = {
                    "success": True,
                    "data": results,
                    "count": len(results),
                    "total": total_count,
                    "offset": offset,
                    "limit": limit,
                    "has_more": (offset + len(results)) < total_count
                }
            else:
                # Режим без пагинации (обратная совместимость)
                log_with_data(logging.INFO, "Поиск завершен",
                             results=len(results),
                             heavy=is_heavy_query,
                             time_ms=time_ms)

                response_data = {
                    "success": True,
                    "data": results,
                    "count": len(results)
                }

            async def json_generator():
                yield json.dumps(response_data, ensure_ascii=False).encode('utf-8')

            return StreamingResponse(
                json_generator(),
                media_type="application/json"
            )

        finally:
            # Освобождаем слот лимитера если был захвачен
            if is_heavy_query:
                await limiter.release()

    except Exception as e:
        # Освобождаем слот при ошибке если был захвачен
        if is_heavy_query:
            await limiter.release()

        log_with_data(logging.ERROR, "Ошибка поиска",
                     endpoint="/api/search",
                     ip=client_ip,
                     error_type=type(e).__name__,
                     error=str(e))
        app_logger.error(f"Traceback: {str(e)}", exc_info=True)

        return {
            "success": False,
            "error": "Ошибка выполнения поиска"
        }
