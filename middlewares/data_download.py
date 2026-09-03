# Version 1.0 - 08.02.2026 GMT
# Data Download Middleware для TlibWebApp
# Описание: ASGI middleware для добавления Content-Disposition: attachment с RFC 5987 кодировкой
#           к файлам, раздаваемым через StaticFiles на пути /data/.
#           Обеспечивает корректное скачивание файлов с кириллическими именами (ZIP, PDF и др.)
#           без буферизации тела ответа — работает как прокси для http.response.start.
#           Использует determine_content_disposition из archive_service для формирования заголовков.

from urllib.parse import unquote
from starlette.datastructures import MutableHeaders

from config import LOCAL_ARCHIVE_PATH
from services.archive_service import determine_content_disposition


class DataDownloadMiddleware:
    """
    ASGI middleware для добавления Content-Disposition: attachment к файлам /data/.
    
    Перехватывает ответы StaticFiles на пути /data/ и модифицирует заголовок Content-Disposition:
    - Извлекает имя файла из URL
    - Формирует RFC 5987/6266 совместимый заголовок (ASCII fallback + filename*=UTF-8'')
    - Заменяет inline на attachment, чтобы браузер скачивал, а не открывал файлы
    
    Ключевые особенности:
    - Чистый ASGI middleware (не BaseHTTPMiddleware) — не буферизует тело ответа
    - Сохраняет Range-запросы, ETag, 304 Not Modified от StaticFiles
    - Корректно обрабатывает кириллицу в именах файлов
    """
    
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Применяем только к HTTP-запросам на /data/ путь
        if scope["type"] != "http" or not scope["path"].startswith(LOCAL_ARCHIVE_PATH + "/"):
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            """Перехватывает http.response.start и модифицирует заголовки."""
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                
                # Извлекаем имя файла из пути (последний сегмент после /)
                filename = unquote(scope["path"].rsplit("/", 1)[-1])
                
                # Формируем Content-Disposition с RFC 5987 кодировкой
                disposition, _ = determine_content_disposition(filename)
                
                # Заменяем inline на attachment (если файл из INLINE_EXTENSIONS)
                if disposition.startswith("inline;"):
                    disposition = disposition.replace("inline;", "attachment;", 1)
                
                # Устанавливаем заголовок
                headers["content-disposition"] = disposition
            
            await send(message)

        # Проксируем запрос через приложение с обёрткой для send
        await self.app(scope, receive, send_wrapper)
