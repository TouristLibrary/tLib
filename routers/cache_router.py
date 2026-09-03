# Version 2.3 - 10.07.2026 09:45:00 GMT
# Cache Router для TlibWebApp
# Описание: API endpoints для eager caching с per-file readiness.
#           POST /prepare - fire-and-forget запуск подготовки кеша.
#           POST /resolve - единый resolve для ВСЕХ типов (pdf, image, track, all_tracks).
# 2.3: _is_hidden — скрытые отчёты (app.state.hidden_reports) не кешируются: /prepare, /resolve
#      и /contents отвечают как для отсутствующего файла (not_found/404).
# 2.1: _validate_archive_name и /resolve body.path переведены на канонический
#      validate_and_resolve_under_base() (§3 — boundary через Path.relative_to).
# 2.2: атрибуция IP/endpoint в _validate_archive_name — security-события по archive_name
#      теперь логируются с реальным client_ip и endpoint (а не ip=unknown).

import os
import urllib.parse
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services.security.path_validation import (
    validate_and_resolve_under_base,
    PathValidationError,
)

# Импорт конфигурации
from config import (
    DATA_DIRECTORY, LOCAL_ARCHIVE_PATH, CACHE_URL_PATH, GEO_ARCHIVE_SUFFIX, CACHE_RETRY_AFTER_MS,
    CACHE_STATUS_READY, CACHE_STATUS_PREPARING, CACHE_STATUS_STARTED,
    CACHE_STATUS_ALREADY_PREPARING, CACHE_STATUS_NOT_FOUND, CACHE_STATUS_NOT_PREPARED,
    CACHE_STATUS_ERROR,
    CACHE_STAGE_STARTING, CACHE_STAGE_CONVERTING,
)

# Импорт сервисов
from services.cache.cache_prepare_service import (
    prepare_archive_cache,
    convert_standalone_pdf,
    is_preparing,
    read_prepare_status,
    _cleanup_stale
)
from services.cache.cache_service import (
    get_cache_dir,
    get_png_dir_path,
    read_meta,
    is_cache_valid,
    is_cache_valid_from_meta
)
from services.cache.cache_pipeline import read_zip_toc

# Импорт логгеров
from logging_config import app_logger

# Создаем роутер
router = APIRouter(prefix="/api/cache", tags=["cache"])


# ============================================================================
# МОДЕЛИ ДАННЫХ
# ============================================================================

class ResolveRequest(BaseModel):
    """Модель запроса для resolve endpoint."""
    path: str = ""     # zip_path файла (пустой для all_tracks)
    kind: str          # "pdf" | "image" | "track" | "all_tracks"


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

def _format_file_list(archive_name: str, items: list[dict]) -> list[dict]:
    """Форматирует список файлов (из TOC или meta) в формат, ожидаемый фронтендом."""
    result = []
    for item in items:
        entry = {
            "name": item.get("zip_path", ""),
            "size": item.get("size", 0),
            "compressed_size": item.get("size", 0),
            "download_url": f"{CACHE_URL_PATH}/{archive_name}/{urllib.parse.quote(item.get('zip_path', ''), safe='')}",
        }
        kind = item.get("kind")
        if kind:
            entry["kind"] = kind
        pages = item.get("pages")
        if pages:
            entry["pages"] = pages
        png_dir = item.get("png_dir")
        if png_dir:
            entry["png_dir"] = f"{archive_name}/{png_dir}"
        result.append(entry)
    return result


def _client_ip(request: Request) -> str:
    """Извлекает IP клиента из запроса; 'unknown' если недоступен."""
    return request.client.host if request.client else "unknown"


def _is_hidden(request: Request, archive_name: str) -> bool:
    """True, если archive_name в списке скрытых отчётов (app.state.hidden_reports).
    Скрытые отчёты не кешируются — /prepare и /resolve отвечают как для отсутствующего файла."""
    hidden = getattr(request.app.state, "hidden_reports", None)
    return bool(hidden) and archive_name.upper() in hidden


def _validate_archive_name(
    archive_name: str,
    *,
    client_ip: str = "unknown",
    endpoint: str = "unknown",
) -> None:
    """
    Проверяет archive_name через канонический валидатор путей (§3).
    Требует basename-only (без слешей и traversal); rounds=0, т.к. Starlette
    уже декодировал path-параметр. Логирует нарушения через security_logger
    с реальным client_ip и endpoint для корректной атрибуции в critical.log.
    """
    try:
        validate_and_resolve_under_base(
            Path(DATA_DIRECTORY),
            archive_name,
            require_basename=True,
            max_decode_rounds=0,
            client_ip=client_ip,
            endpoint=endpoint,
        )
    except PathValidationError as e:
        raise ValueError(e.message) from e


def _check_file_on_disk(cache_dir: Path, archive_name: str, body: ResolveRequest, meta_file: dict | None = None) -> dict | None:
    """
    Проверяет наличие запрошенного файла на диске.
    Возвращает ready-ответ или None.
    
    Если meta_file передан (запись из _meta.json для данного файла), использует
    его данные для быстрого пути: pages для PDF (без glob), cache_path для image
    (без перебора кандидатов). Если meta_file=None — fallback на filesystem.
    """
    if body.kind == "pdf":
        png_dir = get_png_dir_path(archive_name, body.path)
        if meta_file and "pages" in meta_file:
            # Быстрый путь: pages уже известен из meta, не нужен glob("*.png")
            if png_dir.exists() and png_dir.is_dir():
                rel = f"{archive_name}/{png_dir.relative_to(cache_dir).as_posix()}"
                return {"status": CACHE_STATUS_READY, "png_dir": rel, "pages": meta_file["pages"]}
        else:
            # Fallback (во время подготовки кэша, когда meta ещё не записана)
            if png_dir.exists() and png_dir.is_dir():
                pngs = sorted(png_dir.glob("*.png"))
                if pngs:
                    rel = f"{archive_name}/{png_dir.relative_to(cache_dir).as_posix()}"
                    return {"status": CACHE_STATUS_READY, "png_dir": rel, "pages": len(pngs)}

    elif body.kind == "image":
        if meta_file and "cache_path" in meta_file:
            # Быстрый путь: точный путь файла известен из meta, не нужен перебор кандидатов
            candidate = cache_dir / meta_file["cache_path"]
            if candidate.exists() and candidate.is_file():
                actual_rel = candidate.relative_to(cache_dir).as_posix()
                url = f"{CACHE_URL_PATH}/{archive_name}/{actual_rel}"
                return {"status": CACHE_STATUS_READY, "url": url}
        else:
            # Fallback (во время подготовки кэша, когда meta ещё не записана)
            stem = Path(body.path).stem
            parent = Path(body.path).parent
            # Проверяем оригинал первый (коллизия или small image)
            # Затем конвертированный .jpg (обычный случай)
            for candidate in [
                cache_dir / body.path,                 # оригинал
                cache_dir / parent / f"{stem}.jpg",    # конвертированный
            ]:
                if candidate.exists() and candidate.is_file():
                    actual_rel = candidate.relative_to(cache_dir).as_posix()
                    url = f"{CACHE_URL_PATH}/{archive_name}/{actual_rel}"
                    return {"status": CACHE_STATUS_READY, "url": url}

    elif body.kind == "track":
        track_path = cache_dir / body.path
        if track_path.is_file():
            url = f"{CACHE_URL_PATH}/{archive_name}/{body.path}"
            return {"status": CACHE_STATUS_READY, "url": url}

    elif body.kind == "all_tracks":
        geo_path = cache_dir / f"{archive_name}{GEO_ARCHIVE_SUFFIX}"
        if geo_path.exists():
            url = f"{CACHE_URL_PATH}/{archive_name}/{archive_name}{GEO_ARCHIVE_SUFFIX}"
            return {"status": CACHE_STATUS_READY, "url": url}

    return None


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.post("/{archive_name}/prepare")
async def prepare_cache(archive_name: str, request: Request, background_tasks: BackgroundTasks):
    """
    API: fire-and-forget запуск подготовки кеша.
    Ищет и ZIP и standalone PDF.

    Returns:
        {"status": "ready" | "already_preparing" | "started" | "not_found",
         "files": [...]}   # только для ZIP; содержимое из TOC или meta
    """
    try:
        _validate_archive_name(
            archive_name,
            client_ip=_client_ip(request),
            endpoint=f"/api/cache/{archive_name}/prepare",
        )

        if _is_hidden(request, archive_name):
            return JSONResponse({"status": CACHE_STATUS_NOT_FOUND})

        # Определяем source (ZIP или standalone PDF)
        zip_path = Path(DATA_DIRECTORY) / f"{archive_name}.zip"
        pdf_path = Path(DATA_DIRECTORY) / f"{archive_name}.pdf"

        if zip_path.exists():
            source_path = zip_path
        elif pdf_path.exists():
            source_path = pdf_path
        else:
            # Source не найден, проверяем наличие валидного кеша
            meta = read_meta(archive_name)
            if meta is not None and is_cache_valid_from_meta(meta):
                files = _format_file_list(archive_name, meta.get("files", []))
                return JSONResponse({"status": CACHE_STATUS_READY, "files": files})
            return JSONResponse({"status": CACHE_STATUS_NOT_FOUND})

        # Проверяем актуальность кеша
        if is_cache_valid(archive_name, source_path):
            meta = read_meta(archive_name)
            files = _format_file_list(archive_name, meta.get("files", [])) if meta else []
            return JSONResponse({"status": CACHE_STATUS_READY, "files": files})

        collector = getattr(request.app.state, "stats_collector", None)

        if zip_path.exists():
            # Читаем TOC (центральный каталог ZIP) — доли миллисекунды, без extraction
            try:
                toc = read_zip_toc(zip_path)
                toc_files = _format_file_list(archive_name, toc)
            except Exception:
                toc_files = []

            # Проверяем, не идёт ли уже подготовка
            if is_preparing(archive_name):
                return JSONResponse({"status": CACHE_STATUS_ALREADY_PREPARING, "files": toc_files})

            background_tasks.add_task(prepare_archive_cache, archive_name, zip_path, collector)
            return JSONResponse({"status": CACHE_STATUS_STARTED, "files": toc_files})
        else:
            # Standalone PDF — files не включаем (фронтенд не использует)
            if is_preparing(archive_name):
                return JSONResponse({"status": CACHE_STATUS_ALREADY_PREPARING})

            background_tasks.add_task(convert_standalone_pdf, pdf_path, archive_name, collector)
            return JSONResponse({"status": CACHE_STATUS_STARTED})

    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        app_logger.error(f"Error preparing cache for {archive_name}: {e}", exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@router.get("/{archive_name}/contents")
async def get_cache_contents(archive_name: str, request: Request):
    """
    API: список файлов из кеша (_meta.json) в формате, совместимом с /api/archive/.../contents.
    Позволяет фронтенду получить содержимое архива даже если физический ZIP недоступен.
    Пока кеш готовится — 202 (клиент должен получить файлы из /prepare).
    """
    try:
        _validate_archive_name(
            archive_name,
            client_ip=_client_ip(request),
            endpoint=f"/api/cache/{archive_name}/contents",
        )

        if _is_hidden(request, archive_name):
            return JSONResponse({"error": "Cache not found or invalid"}, status_code=404)

        meta = read_meta(archive_name)
        if meta is None or not is_cache_valid_from_meta(meta):
            if is_preparing(archive_name):
                prepare = read_prepare_status(archive_name)
                return JSONResponse({
                    "status": CACHE_STATUS_PREPARING,
                    "stage": prepare.get("stage", ""),
                    "detail": prepare.get("detail", ""),
                    "retry_after": CACHE_RETRY_AFTER_MS,
                }, status_code=202)
            return JSONResponse({"error": "Cache not found or invalid"}, status_code=404)

        return JSONResponse({
            "name": archive_name,
            "files": _format_file_list(archive_name, meta.get("files", [])),
            "download_url": f"{LOCAL_ARCHIVE_PATH}/{archive_name}.zip",
        })
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        app_logger.error(f"Error reading cache contents for {archive_name}: {e}", exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@router.post("/{archive_name}/resolve")
async def resolve_cache_item(archive_name: str, request: Request, body: ResolveRequest, background_tasks: BackgroundTasks):
    """
    API: единый resolve для ВСЕХ типов (pdf, image, track, all_tracks).
    Включая standalone PDF.
    
    Приоритет проверок (4 шага):
    1. Файл на диске + is_cache_valid()? -> ready (+ os.utime для LRU)
    2. _prepare.json есть? -> preparing (подготовка в процессе)
    3. _meta.json есть и актуален? -> error/not_found (подготовка завершена, файл failed/отсутствует)
    4. Авто-триггер: data/{name}.zip -> prepare_archive_cache(), data/{name}.pdf -> convert_standalone_pdf()
    
    Returns:
        - {"status":"ready", "url":"...", "png_dir":"...", "pages":N}
        - {"status":"preparing", "stage":"...", "detail":"...", "retry_after":1000}
        - {"status":"error", "message":"..."}
        - {"status":"not_found"}
        - {"status":"not_prepared"}
    """
    try:
        client_ip = _client_ip(request)
        _validate_archive_name(
            archive_name,
            client_ip=client_ip,
            endpoint=f"/api/cache/{archive_name}/resolve",
        )

        if _is_hidden(request, archive_name):
            return JSONResponse({"status": CACHE_STATUS_NOT_FOUND})

        cache_dir = get_cache_dir(archive_name)

        # --- ШАГ 0: Валидация path (защита от path traversal) ---
        # Guard-only: проверяем boundary через Path.relative_to (§3).
        # rounds=0 — body.path JSON-поле, уже декодировано; % в именах кэша недопустимо перекодировать.
        # После проверки продолжаем использовать body.path как строку (cache_dir нерезолвнутый).
        if body.path:
            try:
                validate_and_resolve_under_base(
                    cache_dir,
                    body.path,
                    require_basename=False,
                    max_decode_rounds=0,
                    client_ip=client_ip,
                    endpoint=f"/api/cache/{archive_name}/resolve",
                )
            except PathValidationError:
                return JSONResponse({"status": CACHE_STATUS_ERROR, "message": "Invalid path"}, status_code=400)
        
        # Читаем meta + prepare ОДИН раз, кэшируем результат валидации
        meta = read_meta(archive_name)
        prepare = read_prepare_status(archive_name)
        preparing = is_preparing(archive_name)
        if not preparing and prepare.get("status") == CACHE_STATUS_PREPARING:
            _cleanup_stale(archive_name)
            prepare = read_prepare_status(archive_name)
        
        # Вычисляем meta_valid ОДИН раз
        if meta is not None:
            meta_valid = is_cache_valid_from_meta(meta)
        else:
            # meta нет: файлы ОК только если prepare активен (per-file readiness)
            # Если prepare НЕ активен (краш?) -- не отдаём, чтобы не вернуть битые файлы
            meta_valid = preparing
        
        # Ищем запись для конкретного файла в meta ОДИН раз — используется в Шагах 1 и 3
        meta_file = None
        if meta and body.path:
            meta_file = next((f for f in meta.get("files", []) if f.get("zip_path") == body.path), None)
        
        # --- ШАГ 1: Файл на диске? + Проверка актуальности кеша ---
        file_found = _check_file_on_disk(cache_dir, archive_name, body, meta_file)
        if file_found:
            if meta_valid:
                # Если именно этот PDF сейчас конвертируется — возвращаем preparing с png_dir,
                # чтобы вьюер мог сразу показать пустые страницы и постепенно их заполнять.
                # Проверяем converting_path чтобы не путать с прогрессом другого PDF в ZIP.
                if preparing and body.kind == "pdf" and prepare.get("converting_path", "") == body.path:
                    file_found["status"] = CACHE_STATUS_PREPARING
                    file_found["pages_total"] = prepare.get("pages_total", 0)
                    file_found["retry_after"] = CACHE_RETRY_AFTER_MS
                    return JSONResponse(file_found)
                # Кеш актуален (или _meta.json ещё не записан -- prepare в процессе)
                try:
                    os.utime(cache_dir, None)  # LRU: обновить mtime
                except Exception:
                    pass
                return JSONResponse(file_found)
            # Кеш устарел (или частичные файлы после краша) -- проваливаемся в шаг 4
        
        # --- ШАГ 2: _prepare.json есть? (подготовка в процессе) ---
        if preparing:
            resp = {
                "status": CACHE_STATUS_PREPARING,
                "stage": prepare.get("stage", ""),
                "detail": prepare.get("detail", ""),
                "retry_after": CACHE_RETRY_AFTER_MS
            }
            return JSONResponse(resp)
        
        # --- ШАГ 3: _meta.json есть? (подготовка завершена, файл не найден) ---
        if meta:
            if meta_valid:
                # Кеш актуален, но файла нет -- ищем причину
                if body.kind == "all_tracks":
                    ga = meta.get("geo_archive", {})
                    if ga.get("status") == CACHE_STATUS_ERROR:
                        return JSONResponse({"status": CACHE_STATUS_ERROR, "message": ga.get("error", "GPS archive error")})
                    # geo.zip нет на диске (шаг 1 не нашёл), но tracks_count есть -- вернуть
                    if "tracks_count" in ga:
                        return JSONResponse({"status": CACHE_STATUS_NOT_FOUND, "tracks_count": ga["tracks_count"]})
                else:
                    # Используем уже найденную запись из meta (вычислена выше)
                    if meta_file and meta_file.get("status") == CACHE_STATUS_ERROR:
                        return JSONResponse({"status": CACHE_STATUS_ERROR, "message": meta_file.get("error", "File error")})
                return JSONResponse({"status": CACHE_STATUS_NOT_FOUND})
            # _meta.json есть, но кеш устарел -- проваливаемся в шаг 4
        
        # --- ШАГ 4: Авто-триггер подготовки ---
        collector = getattr(request.app.state, "stats_collector", None)
        zip_path = Path(DATA_DIRECTORY) / f"{archive_name}.zip"
        if zip_path.exists() and zip_path.is_file():
            background_tasks.add_task(prepare_archive_cache, archive_name, zip_path, collector)
            return JSONResponse({
                "status": CACHE_STATUS_PREPARING,
                "stage": CACHE_STAGE_STARTING,
                "detail": "",
                "retry_after": CACHE_RETRY_AFTER_MS
            })
        
        pdf_path = Path(DATA_DIRECTORY) / f"{archive_name}.pdf"
        if body.kind == "pdf" and pdf_path.exists() and pdf_path.is_file():
            background_tasks.add_task(convert_standalone_pdf, pdf_path, archive_name, collector)
            return JSONResponse({
                "status": CACHE_STATUS_PREPARING,
                "stage": CACHE_STAGE_CONVERTING,
                "detail": "",
                "retry_after": CACHE_RETRY_AFTER_MS
            })
        
        return JSONResponse({"status": CACHE_STATUS_NOT_PREPARED})
        
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        app_logger.error(f"Error resolving cache for {archive_name}: {e}", exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)
