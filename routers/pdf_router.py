# Version 1.0 - 20.01.2026 23:00:00 GMT
# PDF Router для TlibWebApp
# Описание: Роутер для просмотра PDF файлов с опциональным кешированием.
#           Предоставляет endpoint GET /api/pdf/{pdf_name} для просмотра PDF отчётов в браузере.
#           При CACHE_STANDALONE_PDF=True копирует PDF в data.cache/ для ускорения доступа (важно при медленном USB).
#           Выполняет строгую security validation имени файла (защита от Path Traversal) и отдает FileResponse
#           с заголовком Content-Disposition: inline для просмотра в браузере.
#           Поддерживает HTTP кеш-валидаторы (ETag/Last-Modified) и условные запросы (304 Not Modified).

from __future__ import annotations

import urllib.parse
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from config import DATA_DIRECTORY
from logging_config import app_logger, security_logger
from services.archive_service import determine_content_disposition
from services.security.path_validation import PathValidationError, validate_and_resolve_under_base
from services.http_cache_utils import (
    get_path_signature,
    check_not_modified,
)


router = APIRouter(prefix="/api/pdf", tags=["pdf"])


def _is_pdf_filename(filename: str) -> bool:
    """
    Проверяет, что строка похожа на имя PDF файла (без путей) и имеет расширение .pdf.

    Важно: здесь НЕ делаем сложных regex-ограничений на алфавит — это решает is_safe_path,
    а также защита от слешей/абсолютных путей. Нам достаточно гарантировать ".pdf" и "basename".
    """
    if not filename or not isinstance(filename, str):
        return False

    decoded = urllib.parse.unquote(filename)
    if decoded.lower().endswith(".pdf") is False:
        return False

    # Не допускаем, чтобы передали путь вместо имени файла
    return Path(decoded).name == decoded


@router.get("/{pdf_name}")
async def get_pdf(request: Request, pdf_name: str):
    """
    API: просмотр PDF с опциональным кешированием.

    Возвращает FileResponse с Content-Disposition: inline для просмотра в браузере.
    
    Логика:
    - Если CACHE_STANDALONE_PDF=True: копирует PDF в data.cache/, отдает из cache
    - Если CACHE_STANDALONE_PDF=False: отдает напрямую из DATA_DIRECTORY
    - Поддерживает ETag + Last-Modified + 304 Not Modified
    - Поддерживает Range-запросы для навигации по страницам PDF
    """
    try:
        client_ip = request.client.host
        endpoint = f"/api/pdf/{pdf_name}"

        if not _is_pdf_filename(pdf_name):
            security_logger.log_invalid_request(client_ip, endpoint, "Invalid PDF filename")
            return JSONResponse({"error": "Invalid filename"}, status_code=400)

        # Единственный источник правды для filesystem-checks:
        # вернет уже проверенный абсолютный Path внутри DATA_DIRECTORY
        pdf_path = validate_and_resolve_under_base(
            Path(DATA_DIRECTORY),
            pdf_name,
            client_ip=client_ip,
            endpoint=endpoint,
            require_basename=True,
            allowed_suffixes=[".pdf"],
        )

        if not pdf_path.exists() or not pdf_path.is_file():
            return JSONResponse({"error": "File not found"}, status_code=404)

        # Кеш-валидаторы (ETag/Last-Modified) — считаем до кеширования
        pdf_mtime_ns, pdf_size, pdf_mtime_s = get_path_signature(pdf_path)
        etag = f'W/"pdf:{pdf_mtime_ns}-{pdf_size}"'
        early, common_headers = check_not_modified(request, etag, pdf_mtime_s)
        if early:
            return early

        # Отдаём PDF напрямую из DATA_DIRECTORY
        # Кеширование standalone PDF (конвертация в PNG) теперь через prepare/resolve flow
        serve_path = pdf_path

        # Формируем Content-Disposition для inline просмотра
        content_disposition, _ = determine_content_disposition(pdf_path.name)

        # HTTP response formatting: FileResponse для отдачи файла
        # FileResponse автоматически:
        # - Поддерживает Range запросы (докачка и навигация по PDF)
        # - Устанавливает Content-Length
        # - Оптимально буферизует отдачу
        response = FileResponse(
            path=str(serve_path),
            media_type="application/pdf",
            filename=pdf_path.name
        )

        # Добавляем кастомные заголовки кеширования
        for k, v in common_headers.items():
            response.headers[k] = v

        # Перезаписываем Content-Disposition с правильной кодировкой
        response.headers["Content-Disposition"] = content_disposition

        return response

    except PathValidationError as e:
        # Детали уже залогированы валидатором; клиенту возвращаем контролируемую ошибку.
        return JSONResponse({"error": e.message}, status_code=int(e.status_code))
    except Exception as e:
        app_logger.error(f"Ошибка просмотра PDF: {e}", exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)
