# Version 1.6 - 16.06.2026 21:00:00 GMT
# Middleware для добавления заголовков безопасности
# Описание: Добавляет HTTP заголовки безопасности ко всем ответам сервера: X-Content-Type-Options (nosniff),
#           X-Frame-Options (SAMEORIGIN), X-XSS-Protection: 0 (легаси-аудитор отключён, защита через CSP),
#           Referrer-Policy, Strict-Transport-Security, Content-Security-Policy.
#           Для HTML страниц добавляет Cache-Control (CACHE_CONTROL_HTML) для гарантии актуальности.
#           Для статических файлов в /data/* и /cache/* добавляет Cache-Control: private, no-cache, must-revalidate, чтобы браузер мог
#           хранить файл и делать быстрый revalidate (304) вместо повторной загрузки, при этом обновления «по месту» подхватывались.
#           Для JS/CSS файлов (/js/*, /css/*) добавляет Cache-Control: no-cache, чтобы браузер всегда проверял актуальность
#           через conditional request (304), не прибегая к heuristic caching.
#           SEO: X-Robots-Tag: noindex, nofollow для /data/*, /data.db/* (файлы доступны для скачивания,
#           но не индексируются) и для служебных HTML-страниц (NOINDEX_PAGES, включая /admin).
#           1.6: добавлен /admin в NOINDEX_PAGES.
#           Все заголовки настраиваются через config.py (SECURITY_HEADERS, CSP_POLICY, CACHE_CONTROL_HTML, CACHE_CONTROL_STATIC).

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from config import SECURITY_HEADERS, CSP_POLICY, CACHE_CONTROL_HTML, LOCAL_ARCHIVE_PATH, CACHE_URL_PATH, CACHE_CONTROL_REVALIDATE, CACHE_CONTROL_STATIC

# Служебные страницы: доступны публично, но не должны индексироваться
_NOINDEX_PAGES = frozenset({
    "/oldscan.html",
    "/cloud.html",
    "/png-viewer",
    "/login.html",
    "/upload.html",
    "/admin",           # содержимое зависит от роли, индексировать бессмысленно
})


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware для добавления заголовков безопасности ко всем HTTP ответам.
    Защищает от распространенных веб-уязвимостей.
    """
    
    async def dispatch(self, request: Request, call_next):
        """
        Обрабатывает запрос и добавляет заголовки безопасности к ответу
        
        Args:
            request: HTTP запрос
            call_next: Следующий обработчик в цепочке
            
        Returns:
            Response: HTTP ответ с добавленными заголовками безопасности
        """
        # Получаем ответ от следующего обработчика
        response = await call_next(request)

        path = str(request.url.path or "")

        # SEO: блокировка индексации для файловых директорий и служебных страниц.
        # X-Robots-Tag здесь — для StaticFiles-маршрутов (/data/, /data.db/) и
        # служебных HTML-страниц (NOINDEX_PAGES). Для root() noindex при фильтрах
        # выставляется непосредственно в роутере.
        if (path.startswith("/data/") or path.startswith("/data.db/")
                or path in _NOINDEX_PAGES):
            response.headers["X-Robots-Tag"] = "noindex, nofollow"

        # Добавляем базовые заголовки безопасности из конфигурации
        for header_name, header_value in SECURITY_HEADERS.items():
            response.headers[header_name] = header_value
        
        # Добавляем Content Security Policy из конфигурации
        response.headers["Content-Security-Policy"] = CSP_POLICY
        
        # Добавляем Cache-Control для HTML страниц
        # Браузер будет всегда проверять актуальность страницы на сервере
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            response.headers["Cache-Control"] = CACHE_CONTROL_HTML
        else:
            # Для статических файлов /data/* включаем revalidate-кеширование:
            # - файл может быть закеширован локально
            # - при повторном открытии браузер делает условный запрос и получает 304 без тела
            # - при замене файла «по месту» ETag/Last-Modified меняются и браузер подхватывает обновление
            if (path == LOCAL_ARCHIVE_PATH or path.startswith(f"{LOCAL_ARCHIVE_PATH}/")
                    or path.startswith(f"{CACHE_URL_PATH}/")):
                response.headers["Cache-Control"] = CACHE_CONTROL_REVALIDATE
            elif path.startswith('/js/') or path.startswith('/css/'):
                response.headers["Cache-Control"] = CACHE_CONTROL_STATIC
        
        return response

