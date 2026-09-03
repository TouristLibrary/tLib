# Version 5.0 - 10.01.2026 20:21:09 GMT
# Archive Router для TlibWebApp
# Описание: Тонкий HTTP-слой для работы с ZIP архивами. Предоставляет три endpoint'а:
#           GET /api/archive/{filename}/contents - получение списка файлов в архиве с метаданными,
#           GET /api/archive/{filename}/file/{filepath} - извлечение файла из архива в кеш и отдача через FileResponse,
#           GET /api/archive/{filename}/all-tracks - отдача ZIP архива со всеми GPS-треками (с кэшированием в data.cache/).
#           Извлеченные файлы кешируются в data.cache/ и отдаются через FileResponse для поддержки Range запросов
#           и оптимальной буферизации. Добавляет HTTP кеш-валидаторы (ETag/Last-Modified) и поддерживает условные
#           запросы (304) для ускорения повторных открытий без повторной загрузки.
#           Роутер выполняет только security validation (Path Traversal, directory checks).
#           Вся бизнес-логика (размеры, фильтрация, кэширование, LRU-очистка) в services/archive_service.py.

import zipfile
import json
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, JSONResponse

# Импорт services
from services import archive_service
from services.file_service import decode_zip_filename
from services.security.path_validation import (
    PathValidationError,
    decode_url_path,
    validate_and_resolve_under_base,
    validate_zip_member_path,
)

# Импорт конфигурации
from config import (
    DATA_DIRECTORY, MIME_TYPES, MAX_FILE_SIZE, FILTER_MACOS_METADATA,
    DANGEROUS_INLINE_EXTENSIONS, STRICT_FILE_CSP,
    GEO_ARCHIVE_SUFFIX, DEFAULT_MIME_TYPE
)

# Импорт логгеров
from logging_config import app_logger

# Импорт HTTP cache-утилит
from services.http_cache_utils import (
    get_path_signature,
    check_not_modified,
)

# Создаем роутер
router = APIRouter(prefix="/api/archive", tags=["archive"])


def _find_zipinfo_for_decoded_path(zip_path: Path, filepath_decoded: str) -> zipfile.ZipInfo | None:
    """
    Находит ZipInfo для файла внутри ZIP, сопоставляя как исходное имя, так и декодированное.

    Важно: не читает содержимое файла (только метаданные).
    """
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            try:
                info = zf.getinfo(filepath_decoded)
                if not info.is_dir():
                    return info
            except KeyError:
                pass

            for info in zf.infolist():
                if info.is_dir():
                    continue

                # Ограничение размера — аналогично сервису (чтобы раньше отсекать большие файлы)
                if info.file_size > MAX_FILE_SIZE:
                    continue

                if FILTER_MACOS_METADATA:
                    try:
                        if archive_service.is_macos_metadata_file(info.filename):
                            continue
                    except Exception:
                        # Если helper недоступен по импорту/названию — просто не фильтруем здесь.
                        pass

                decoded_name = decode_zip_filename(info.filename)
                if decoded_name == filepath_decoded or info.filename == filepath_decoded:
                    return info

        return None
    except Exception:
        return None


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (SECURITY LAYER)
# ============================================================================

def _validate_archive_filename(filename: str, client_ip: str, endpoint: str):
    """
    Security validation для имени архива.
    
    Выполняет только проверки безопасности (security layer):
    - Декодирование URL-encoded имени
    - Проверка на Path Traversal атаки
    - Проверка, что файл находится в разрешенной директории
    
    Бизнес-проверки (существование, размер) выполняются в archive_service.
    
    Args:
        filename: Имя архива (может быть URL-encoded)
        client_ip: IP адрес клиента для логирования
        endpoint: Endpoint запроса для логирования
        
    Returns:
        tuple: (zip_path: Path, error_response: Response)
               - Если security проверки пройдены: (Path к архиву, None)
               - Если ошибка безопасности: (None, Response с ошибкой)
    """
    # 1. Декодируем имя (с учётом возможного double-encoding)
    filename_decoded = decode_url_path(filename)

    # 2. Единственный источник правды для filesystem-checks:
    # валидируем и получаем уже безопасный zip_path внутри DATA_DIRECTORY
    try:
        zip_path = validate_and_resolve_under_base(
            Path(DATA_DIRECTORY),
            f"{filename_decoded}.zip",
            client_ip=client_ip,
            endpoint=endpoint,
            require_basename=True,
            allowed_suffixes=[".zip"],
        )
    except PathValidationError as e:
        # Детали уже залогированы валидатором, здесь просто отдаём контролируемый ответ
        return None, JSONResponse({"error": e.message}, status_code=int(e.status_code))
    
    # 5. Вызов сервиса для business validation
    success, result = archive_service.validate_archive_business_rules(
        zip_path, client_ip, filename_decoded
    )
    if not success:
        return None, JSONResponse(
            {"error": result["message"]},
            status_code=result["status_code"]
        )
    
    # Security и business проверки пройдены
    return result, None  # result = validated zip_path


# ============================================================================
# ENDPOINT 1: ПОЛУЧЕНИЕ СОДЕРЖИМОГО АРХИВА
# ============================================================================

@router.get("/{filename}/contents")
async def get_archive_contents(request: Request, filename: str):
    """
    API: возвращает список файлов в архиве.
    
    Тонкий слой: security validation → service call → HTTP response formatting.
    """
    try:
        client_ip = request.client.host
        endpoint = f"/api/archive/{filename}/contents"
        
        # Security validation (роутер)
        zip_path, error_response = _validate_archive_filename(filename, client_ip, endpoint)
        if error_response:
            return error_response
        
        # Business logic (сервис)
        success, result = await archive_service.get_archive_file_list(zip_path, filename)
        if not success:
            return JSONResponse({"error": result}, status_code=400)
        
        # HTTP response formatting
        response_json = json.dumps(result, ensure_ascii=False, indent=2)
        return Response(
            content=response_json,
            media_type="application/json; charset=utf-8"
        )
        
    except Exception as e:
        # БЕЗОПАСНОСТЬ: Логируем детали только в логи, клиенту - общее сообщение
        app_logger.error(f"Ошибка получения содержимого архива: {e}", exc_info=True)
        return JSONResponse(
            {"error": "Internal server error"},
            status_code=500
        )


# ============================================================================
# ENDPOINT 2: ИЗВЛЕЧЕНИЕ ФАЙЛА ИЗ АРХИВА
# ============================================================================

@router.api_route("/{filename}/file/{filepath:path}", methods=["GET", "HEAD"])
async def get_file_from_archive(request: Request, filename: str, filepath: str, original: bool = False):
    """
    API: извлекает и возвращает файл из архива.
    
    Параметры:
    - original: если True, возвращает оригинальный файл напрямую из ZIP (без кеширования)
               При недоступности data/ возвращает 503 с JSON {error, message, download_url}
    
    Тонкий слой: security validation → service call → HTTP response formatting.
    """
    try:
        client_ip = request.client.host
        # Валидируем путь внутри ZIP (string-only) и используем decoded версию в endpoint для логов.
        filepath_decoded = validate_zip_member_path(filepath, client_ip=client_ip, endpoint="/api/archive/*/file/*")
        endpoint = f"/api/archive/{filename}/file/{filepath_decoded}"
        
        # Security validation (роутер): архив
        zip_path, error_response = _validate_archive_filename(filename, client_ip, endpoint)
        if error_response:
            return error_response
        
        # filepath_decoded уже провалидирован как zip-member path выше.

        # Разделяем логику: для ?original=1 используем ZIP, для cache - кешированный файл
        if original:
            # === ВЕТКА: Оригинал из ZIP ===
            # Проверяем доступность data/ directory
            if not Path(DATA_DIRECTORY).exists():
                # data/ недоступна - возвращаем 503 с информацией
                download_url = f"/data/{filename}.zip"
                return JSONResponse({
                    "error": "data_unavailable",
                    "message": "Source data directory is not available",
                    "download_url": download_url
                }, status_code=503)
            
            # Кеш-валидаторы из ZIP
            zip_mtime_ns, zip_size, zip_mtime_s = get_path_signature(zip_path)
            info = _find_zipinfo_for_decoded_path(zip_path, filepath_decoded)
            if info is None:
                return JSONResponse({"error": "File not found"}, status_code=404)

            # Доп. проверка размера (fail-fast)
            if info.file_size > MAX_FILE_SIZE:
                return JSONResponse({"error": "File too large"}, status_code=400)

            etag = f'W/"zip:{zip_mtime_ns}-{zip_size};entry:{int(info.CRC)}-{int(info.file_size)}"'
            early, common_headers = check_not_modified(request, etag, zip_mtime_s)
            if early:
                return early
            
            # Стримим из ZIP
            success, result = await archive_service.stream_original_from_archive(zip_path, filepath_decoded)
            if not success:
                status_code = 404 if result == "File not found" else 400
                return JSONResponse({"error": result}, status_code=status_code)
            
            # result теперь bytes с содержимым файла
            file_bytes = result
            
            # Determine MIME type and Content-Disposition
            ext = Path(filepath_decoded).suffix.lower()
            content_type = MIME_TYPES.get(ext, DEFAULT_MIME_TYPE)
            content_disposition, _ = archive_service.determine_content_disposition(filepath_decoded)
            
            # HTTP response: стримим байты напрямую
            response = Response(
                content=file_bytes,
                media_type=content_type,
                headers=common_headers
            )
            
            response.headers["Content-Disposition"] = content_disposition
            
            # Строгий CSP для файлов, которые могут содержать скрипты
            if ext in DANGEROUS_INLINE_EXTENSIONS:
                response.headers["Content-Security-Policy"] = STRICT_FILE_CSP
            
            return response
        
        else:
            # === ВЕТКА: Отдача из кеша ===
            # resolve уже вернул URL с фактическим расширением (например .jpg для конвертированного)
            from services.cache.cache_service import get_cache_dir
            
            cache_dir = get_cache_dir(filename)
            cached_file_path = cache_dir / filepath_decoded
            
            # Если файл не найден -> 404
            if not cached_file_path.exists() or not cached_file_path.is_file():
                return JSONResponse({"error": "File not found in cache"}, status_code=404)
            
            # Кеш-валидаторы из кешированного файла
            cache_mtime_ns, cache_size, cache_mtime_s = get_path_signature(cached_file_path)
            etag = f'W/"cache:{cache_mtime_ns}-{cache_size}"'
            early, common_headers = check_not_modified(request, etag, cache_mtime_s)
            if early:
                return early
            
            # Determine MIME type по расширению файла на диске
            ext = cached_file_path.suffix.lower()
            content_type = MIME_TYPES.get(ext, DEFAULT_MIME_TYPE)
            content_disposition, _ = archive_service.determine_content_disposition(filepath_decoded)
            
            # HTTP response formatting: FileResponse для отдачи из кеша
            response = FileResponse(
                path=str(cached_file_path),
                media_type=content_type,
                filename=Path(filepath_decoded).name
            )
            
            # Добавляем кастомные заголовки кеширования
            for k, v in common_headers.items():
                response.headers[k] = v
            
            # Перезаписываем Content-Disposition с правильной кодировкой
            response.headers["Content-Disposition"] = content_disposition
            
            # Строгий CSP для файлов, которые могут содержать скрипты
            if ext in DANGEROUS_INLINE_EXTENSIONS:
                response.headers["Content-Security-Policy"] = STRICT_FILE_CSP
            
            return response
        
    except Exception as e:
        # БЕЗОПАСНОСТЬ: Логируем детали только в логи
        app_logger.error(f"Ошибка извлечения файла: {e}", exc_info=True)
        return JSONResponse(
            {"error": "Internal server error"},
            status_code=500
        )


# ============================================================================
# ENDPOINT 3: АРХИВ GPS-ТРЕКОВ
# ============================================================================

@router.get("/{filename}/all-tracks")
async def get_all_tracks_archive(request: Request, filename: str):
    """
    API: отдаёт готовый GPS-архив из кеша (не создаёт).
    GPS-архив создаётся при prepare, а не при запросе.
    
    Тонкий слой: security validation → cache lookup → HTTP response formatting.
    """
    try:
        client_ip = request.client.host
        endpoint = f"/api/archive/{filename}/all-tracks"
        
        # Security validation (роутер)
        zip_path, error_response = _validate_archive_filename(filename, client_ip, endpoint)
        if error_response:
            return error_response
        
        # Ищем готовый GPS-архив в кеше
        from services.cache.cache_service import get_cache_dir
        
        cache_dir = get_cache_dir(filename)
        geo_path = cache_dir / f"{filename}{GEO_ARCHIVE_SUFFIX}"
        
        # Если файл не найден -> 404 (prepare ещё не завершён или треков нет)
        if not geo_path.exists():
            return JSONResponse({"error": "GPS tracks not ready"}, status_code=404)

        zip_mtime_ns, zip_size, zip_mtime_s = get_path_signature(zip_path)
        
        # Подсчитываем треки для ETag
        try:
            with zipfile.ZipFile(geo_path, "r") as zf:
                track_count = len([f for f in zf.namelist() if not f.endswith("/")])
        except Exception:
            track_count = 0
        
        etag = f'W/"alltracks:{zip_mtime_ns}-{zip_size};tracks:{int(track_count)}"'
        early, common_headers = check_not_modified(request, etag, zip_mtime_s)
        if early:
            return early
        
        # HTTP response formatting: file response (без чтения в память)
        archive_name = zip_path.stem
        download_filename = f"{archive_name}{GEO_ARCHIVE_SUFFIX}"

        response = FileResponse(
            path=str(geo_path),
            media_type="application/zip",
            filename=download_filename,
        )
        # FileResponse сам выставляет часть заголовков, но мы хотим гарантировать revalidate + наш ETag.
        for k, v in common_headers.items():
            response.headers[k] = v
        response.headers["Content-Disposition"] = f'attachment; filename="{download_filename}"'
        return response
        
    except Exception as e:
        app_logger.error(f"Ошибка отдачи GPS архива: {e}", exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)
