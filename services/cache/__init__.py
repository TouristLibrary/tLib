# Version 1.0 - 21.02.2026 00:00:00 GMT
# Cache package для TlibWebApp
# Описание: Пакет содержит сервисы управления кешем архивов
# - cache_service.py: Пути кеша, чтение meta, LRU-очистка, атомарная запись JSON
# - cache_prepare_service.py: Подготовка кеша (lock, extraction, conversion, meta write)
# - cache_pipeline.py: Конвертационные шаги (extraction, GPS, PDF, images, meta)
