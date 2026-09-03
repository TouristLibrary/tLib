# Version 3.1 - 21.02.2026 00:00:00 GMT
# Services package для TlibWebApp
# Описание: Пакет содержит бизнес-логику приложения
# - file_service.py: Работа с файлами (декодирование имен с поддержкой кириллицы, фильтрация macOS файлов)
# - archive_service.py: Бизнес-логика работы с ZIP архивами и GPS-треками
# - http_cache_utils.py: HTTP кеш-утилиты (ETag, Last-Modified, 304)
# - database/: Работа с базой данных SQLite (query_builder, reference_loader, search_limiter)
# - file_watcher/: Staged pipeline обработки загруженных файлов
# - validation/: Валидация данных (encoding, JSON schema, конвертация JSON → SQLite)
# - conversion/: Конвертация медиа-файлов (изображения → JPG, PDF → PNG; требуют PyMuPDF)
# - cache/: Управление кешем архивов (пути, LRU-очистка, подготовка, pipeline)
