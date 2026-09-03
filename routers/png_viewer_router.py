# Version 2.1 - 21.06.2026 19:00:00 GMT
# PNG Viewer Router для TlibWebApp
# Описание: API endpoints для PNG viewer. Предоставляет листинг PNG директорий в data.cache
#           и списки PNG файлов для просмотра. Используется embedded-вьюером /png-viewer.
#           Логика resolve переехала в единый cache_router POST /resolve.
# 2.1: /pages переведён на канонический validate_and_resolve_under_base() (§3);
#      _is_safe_dirname и ручная startswith-проверка удалены;
#      _list_png_files использует .resolve() базы для корректного relative_to.

import json
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

# Импорт конфигурации
from config import CACHE_DIRECTORY, CACHE_URL_PATH, CACHE_META_FILENAME

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
async def get_pages(dir_path: str, request: Request):
    """
    API: получить список PNG страниц в директории.

    Разрешает вложенные пути (>= 2 сегментов).
    Последний сегмент должен быть PNG-директорией (заканчивается на -png).
    Boundary проверка — через канонический validate_and_resolve_under_base() (§3).

    Args:
        dir_path: путь к директории (например: "12345-ABC/dir1/report-png")

    Returns:
        {"pages": [{"name": "...", "url": "...", "size": N}, ...], "total": N}
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

        # Получаем список файлов (full_path resolved → _list_png_files использует resolved базу)
        pages = _list_png_files(full_path)

        # Читаем маркер общего числа страниц (записывается pre-scan'ом)
        pages_total = None
        pages_total_file = full_path / "_pages_total.txt"
        if pages_total_file.exists():
            try:
                pages_total = int(pages_total_file.read_text().strip())
            except (ValueError, OSError):
                pass

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
