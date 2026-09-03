# Version 1.0 - 21.02.2026 00:00:00 GMT
# Конфигурация кеша TlibWebApp
# Описание: Имена файлов и директорий кеша, статусы, стадии подготовки,
#           таймауты и параметры LRU-очистки.

# ==================== ФАЙЛЫ И ДИРЕКТОРИИ КЕША ====================

# Имя файла метаданных кеша архива
# Хранит информацию о содержимом и актуальности кеша
CACHE_META_FILENAME: str = "_meta.json"

# Имя рабочей директории для промежуточных файлов кеша
# Используется при извлечении и конвертации файлов из архивов
CACHE_WORK_DIRNAME: str = "_work"

# Имя файла статуса подготовки кеша
# Содержит информацию о процессе подготовки (stage, detail, updated_at)
CACHE_PREPARE_STATUS_FILENAME: str = "_prepare.json"

# Имя директории блокировки для подготовки кеша
# Используется для атомарной блокировки через mkdir
CACHE_LOCK_DIRNAME: str = "_prepare.lockdir"

# Суффикс имени файла GPS-архива (добавляется к имени архива)
# Результат: {archive_name}-geo.zip
GEO_ARCHIVE_SUFFIX: str = "-geo.zip"

# ==================== РАЗМЕРЫ И ТАЙМАУТЫ КЕША ====================

# Максимальный размер всего кеша data.cache/ (GPS архивы + извлеченные файлы)
MAX_CACHE_SIZE: int = 150 * 1024 * 1024 * 1024  # 150 ГБ

# Допуск при сравнении mtime файлов (секунды)
# Файловые системы могут округлять время модификации
MTIME_TOLERANCE: float = 0.1

# Таймаут для протухшей блокировки кеша (минуты)
# Если heartbeat не обновлялся дольше этого — lock считается stale
CACHE_STALE_LOCK_TIMEOUT_MINUTES: int = 5

# Retry-After для клиента при подготовке кеша (миллисекунды)
CACHE_RETRY_AFTER_MS: int = 1000

# Множитель для оценки размера кеша ZIP (сжатый размер * множитель)
CACHE_ZIP_SIZE_MULTIPLIER: float = 1.5

# Множитель для оценки размера кеша standalone PDF (размер PDF * множитель)
CACHE_PDF_SIZE_MULTIPLIER: float = 3.0


# ==================== СТАТУСЫ КЕША ====================

# Статусы кеша (API-контракт между cache_router, cache_prepare_service, cache_pipeline)
CACHE_STATUS_READY: str = "ready"
CACHE_STATUS_PREPARING: str = "preparing"
CACHE_STATUS_STARTED: str = "started"
CACHE_STATUS_ALREADY_PREPARING: str = "already_preparing"
CACHE_STATUS_NOT_FOUND: str = "not_found"
CACHE_STATUS_NOT_PREPARED: str = "not_prepared"
CACHE_STATUS_ERROR: str = "error"
CACHE_STATUS_NONE: str = "none"

# Стадии подготовки кеша
CACHE_STAGE_STARTING: str = "starting"
CACHE_STAGE_EXTRACTING: str = "extracting"
CACHE_STAGE_CONVERTING: str = "converting"
