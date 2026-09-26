# Version 2.5 - 26.09.2026 09:00:00 GMT
# PNG Viewer Router для TlibWebApp
# Описание: API endpoints для PNG viewer. Предоставляет листинг PNG директорий в data.cache
#           и списки PNG файлов для просмотра. Используется embedded-вьюером /png-viewer.
#           Логика resolve переехала в единый cache_router POST /resolve.
# 2.5: рендер PDF по окну просмотра — /pages единственный триггер докрутки; решение «докручивать ли»
#      (гистерезис по окну) — в resume_pdf_conversion, которая больше не учитывает «В кэш».
# 2.4: имя архива и путь директории для heartbeat/докрутки берутся из проверенного full_path.
# 2.3: /pages обновляет mtime папки архива (touch_watch) — LRU не вытесняет читаемый PDF.
# 2.2: /pages — heartbeat просмотра для конвертации PDF, пока смотрят: пишет _watch.json
#      (параметр want — страница, которую показывает вьюер) и возобновляет частичную
#      конвертацию (resume_pdf_conversion), если она на паузе.
# 2.1: /pages переведён на канонический validate_and_resolve_under_base() (§3);
#      _is_safe_dirname и ручная startswith-проверка удалены;
#      _list_png_files использует .resolve() базы для корректного relative_to.

import json
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

# Импорт конфигурации
from config import CACHE_DIRECTORY, CACHE_URL_PATH, CACHE_META_FILENAME

# Импорт сервисов кеша: heartbeat просмотра и докрутка частичной конвертации
from services.cache.cache_watch import touch_watch, read_pages_total, is_partial
from services.cache.cache_prepare_service import is_preparing, resume_pdf_conversion

# Импорт канонического валидатора путей
from services.security.path_validation import (
    validate_and_resolve_under_base,
    PathValidationError,
)

# Импорт логгеров
from logging_config import app_logger

# Создаем роутер
router = APIRouter(prefix="/api/png", tags=["png-viewer"])


def _scan_png_directories() -> list[dict]:
    """
    Сканирует data.cache на наличие PNG директорий.

    Читает _meta.json каждого архива вместо рекурсивного обхода файловой системы.
    Fallback на filesystem-scan для архивов без _meta.json.

    Returns:
        Список словарей с информацией о директориях:
        [{"name": "12345-ABC-png", "path": "12345-ABC/dir1/report-png", "page_count": 47}, ...]
    """
    cache_dir = Path(CACHE_DIRECTORY)

    if not cache_dir.exists():
        return []

    png_dirs = []

    for folder_path in cache_dir.iterdir():
        if not folder_path.is_dir():
            continue

        meta_path = folder_path / CACHE_META_FILENAME
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            for f in meta.get("files", []):
                if f.get("kind") != "pdf" or "png_dir" not in f:
                    continue
                pages = f.get("pages", 0)
                if pages <= 0:
                    continue
                png_dirs.append({
                    "name": Path(f["png_dir"]).name,
                    "path": f"{folder_path.name}/{f['png_dir']}",
                    "page_count": pages,
                })
        except Exception:
            # Fallback: прямое сканирование для архивов без _meta.json
            for png_dir in folder_path.rglob('*'):
                if not png_dir.is_dir() or not png_dir.name.endswith('-png'):
                    continue
                png_files = list(png_dir.glob('*.png'))
                if not png_files:
                    continue
                try:
                    relative_path = png_dir.relative_to(cache_dir)
                    png_dirs.append({
                        'name': png_dir.name,
                        'path': str(relative_path.as_posix()),
                        'page_count': len(png_files),
                    })
                except ValueError:
                    continue

    png_dirs.sort(key=lambda x: x['path'])
    return png_dirs


def _list_png_files(dir_path: Path) -> list[dict]:
    """
    Возвращает список PNG файлов в директории.

    dir_path ожидается уже resolved (абсолютный), поэтому база для relative_to
    тоже должна быть resolved — иначе Path.relative_to бросит ValueError.

    Args:
        dir_path: абсолютный (resolved) путь к директории

    Returns:
        Список словарей с информацией о файлах:
        [{"name": "document_0001.png", "url": "/cache/...", "size": 123456}, ...]
    """
    if not dir_path.exists() or not dir_path.is_dir():
        return []

    cache_resolved = Path(CACHE_DIRECTORY).resolve()
    png_files = []

    for png_file in dir_path.glob('*.png'):
        if not png_file.is_file():
            continue

        # Формируем URL относительно cache mount point
        relative_path = png_file.relative_to(cache_resolved)
        url = f"{CACHE_URL_PATH}/{relative_path.as_posix()}"

        try:
            size = png_file.stat().st_size
        except Exception:
            size = 0

        png_files.append({
            'name': png_file.name,
            'url': url,
            'size': size
        })

    # Сортируем по имени (важно для правильного порядка страниц)
    png_files.sort(key=lambda x: x['name'])

    return png_files


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("/directories")
async def get_directories(request: Request):
    """
    API: получить список PNG директорий в data.cache.
    
    Returns:
        {"directories": [{"name": "...", "path": "...", "page_count": N}, ...]}
    """
    try:
        directories = _scan_png_directories()
        
        app_logger.debug(f"PNG directories scan: found {len(directories)} directories")
        
        return JSONResponse({
            'directories': directories,
            'total': len(directories)
        })
        
    except Exception as e:
        app_logger.error(f"Error scanning PNG directories: {e}", exc_info=True)
        return JSONResponse({'error': 'Internal server error'}, status_code=500)


@router.get("/{dir_path:path}/pages")
async def get_pages(
    dir_path: str,
    request: Request,
    background_tasks: BackgroundTasks,
    want: Optional[int] = None,
):
    """
    API: получить список PNG страниц в директории.

    Разрешает вложенные пути (>= 2 сегментов).
    Последний сегмент должен быть PNG-директорией (заканчивается на -png).
    Boundary проверка — через канонический validate_and_resolve_under_base() (§3).

    Запрос — heartbeat просмотра: png-viewer опрашивает /pages раз в 2 с, пока страниц
    на диске меньше pages_total. Конвертер рендерит окно страниц от want (первой — саму want);
    частичная конвертация на паузе возобновляется, когда впереди от want готово меньше
    половины окна (единственный триггер докрутки).

    Args:
        dir_path: путь к директории (например: "12345-ABC/dir1/report-png")
        want: страница (1-based), которую показывает вьюер

    Returns:
        {"pages": [{"name": "...", "url": "...", "size": N}, ...], "total": N,
         "directory": "...", "pages_total": N}   # pages_total — если известен из pre-scan
    """
    try:
        client_ip = request.client.host if request.client else "unknown"
        endpoint = f"/api/png/{dir_path}/pages"

        # Бизнес-правило 1: минимум 2 сегмента
        parts = dir_path.split('/')
        if len(parts) < 2:
            return JSONResponse(
                {'error': 'Invalid directory path format (need at least 2 segments)'},
                status_code=400,
            )

        # Бизнес-правило 2: последний сегмент — PNG-директория
        if not parts[-1].endswith('-png'):
            return JSONResponse(
                {'error': 'Last segment must be PNG directory (ending with -png)'},
                status_code=400,
            )

        # Безопасность: canonical boundary-check через Path.relative_to (§3).
        # dir_path берётся из URL-параметра, поэтому URL_DECODE_MAX_ROUNDS применяется.
        try:
            full_path = validate_and_resolve_under_base(
                Path(CACHE_DIRECTORY),
                dir_path,
                require_basename=False,
                client_ip=client_ip,
                endpoint=endpoint,
            )
        except PathValidationError:
            return JSONResponse({'error': 'Invalid path'}, status_code=400)

        # Проверяем существование
        if not full_path.exists():
            return JSONResponse({'error': 'Directory not found'}, status_code=404)

        if not full_path.is_dir():
            return JSONResponse({'error': 'Not a directory'}, status_code=400)

        # Имя архива и путь PNG-директории — из проверенного full_path, а не из сырых сегментов URL
        cache_root = Path(CACHE_DIRECTORY).resolve()
        rel_parts = full_path.relative_to(cache_root).parts
        archive_name = rel_parts[0]
        png_dir_rel = "/".join(rel_parts[1:])

        # Heartbeat просмотра: конвертер PDF работает, пока директорию смотрят; заодно LRU-метка архива
        touch_watch(full_path, want if want is not None and want >= 1 else None, cache_root / archive_name)
        if is_partial(full_path) and not is_preparing(archive_name):
            background_tasks.add_task(resume_pdf_conversion, archive_name, png_dir_rel)

        # Получаем список файлов (full_path resolved → _list_png_files использует resolved базу)
        pages = _list_png_files(full_path)

        # Маркер общего числа страниц (записывается pre-scan'ом)
        pages_total = read_pages_total(full_path)

        app_logger.debug(f"PNG pages listing: {dir_path} - {len(pages)} pages")

        response_data = {
            'pages': pages,
            'total': len(pages),
            'directory': dir_path,
        }
        if pages_total is not None:
            response_data['pages_total'] = pages_total

        return JSONResponse(response_data)

    except Exception as e:
        app_logger.error(f"Error listing PNG pages: {e}", exc_info=True)
        return JSONResponse({'error': 'Internal server error'}, status_code=500)
