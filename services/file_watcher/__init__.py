# Version 2.4 - 14.05.2026 00:00:00 GMT
# File Watcher Package
#
# Текущая функциональность:
# Пакет File Watcher для автоматической обработки загружаемых файлов.
# Точка входа pipeline: data.up/20_go/ (staging: data.up/10_up/).
# Модули:
# - utils.py: вспомогательные функции, нормализация ID (Шифр → 5 цифр, ДопШифр → UPPERCASE)
# - pipeline.py: оркестрация staged pipeline
# - deleter.py: обработка операций удаления
# - scanner.py: сканирование, группировка по нормализованному ID, детект дубликатов
# - stability.py: групповой stability-window (защита от недозалитых файлов)
# - validation.py: валидация JSON и ZIP файлов
# - file_operations.py: файловые операции (с переименованием в каноническую форму)
# - database_generator.py: генерация БД
# - publisher.py: публикация результатов
