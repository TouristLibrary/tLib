# Version 1.1 - 25.09.2026 12:00:00 GMT
# Cache package для TlibWebApp
# Описание: Пакет содержит сервисы управления кешем архивов
# - cache_service.py: Пути кеша, чтение meta, LRU-очистка, атомарная запись JSON
# - cache_prepare_service.py: Подготовка кеша (lock, extraction, conversion, meta write)
# - cache_pipeline.py: Конвертационные шаги (extraction, GPS, PDF, images, meta)
# - cache_watch.py: Heartbeat просмотра PNG-директории (_watch.json) и её частичное состояние
