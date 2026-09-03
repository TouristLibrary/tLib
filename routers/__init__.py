# Version 1.6 - 21.02.2026 GMT
# Routers package для TlibWebApp
# Описание: Пакет содержит все API роутеры приложения.
#           Включает роутеры для архивов, поиска, справочников, конфигурации, статических страниц,
#           health check endpoint, cache management, PDF viewer и PNG viewer.
#           Таблица редиректов (/api/redirect-table) обрабатывается в static_router.
#           Скачивание файлов обрабатывается через DataDownloadMiddleware.

from . import archive_router
from . import search_router
from . import lists_router
from . import config_router
from . import static_router
from . import health_router
from . import cache_router
from . import pdf_router
from . import png_viewer_router
from . import admin_router
from . import auth_router
from . import upload_router

__all__ = [
    'archive_router',
    'search_router',
    'lists_router',
    'config_router',
    'static_router',
    'health_router',
    'cache_router',
    'pdf_router',
    'png_viewer_router',
    'admin_router',
    'auth_router',
    'upload_router',
]
