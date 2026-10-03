# Version 1.1 - 03.10.2026 09:36:10 GMT
# Conversion package для TlibWebApp
# Описание: Пакет содержит сервисы конвертации медиа-файлов (требуют PyMuPDF)
# - image_conversion_service.py: Оптимизация изображений (ресайз, JPG/PNG)
# - pdf_to_png_service.py: Конвертация PDF страниц в PNG
# - pdf_render_worker.py: Рендер страницы PDF в процессе-воркере (только stdlib + fitz)
# Инвариант: __init__ пакетов services и services/conversion остаются без импортов —
# spawn-воркер импортирует их по пути к pdf_render_worker и иначе подтянул бы
# config/logging_config (второй процесс открыл бы app.log/critical.log).
# 1.1: pdf_render_worker.py и инвариант пустых __init__.
