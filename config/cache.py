# Version 1.2 - 25.09.2026 12:00:00 GMT
# Конфигурация кеша TlibWebApp
# Описание: Имена файлов и директорий кеша, статусы, стадии подготовки,
#           таймауты и параметры LRU-очистки.
# 1.1: CACHE_RESOLVE_KINDS — допустимые kind у /api/cache/{name}/resolve (pdf убран).
# 1.2: конвертация PDF, пока смотрят — PNG_WATCH_FILENAME, PNG_PAGES_TOTAL_FILENAME,
#      PDF_CONVERT_IDLE_TIMEOUT_SECONDS, CACHE_FILE_STATUS_PARTIAL.

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

# Heartbeat просмотра в PNG-директории: {"ts": unix-время, "want": страница 1-based | null}.
# Пишет GET /api/png/.../pages (png-viewer опрашивает его раз в 2 с),
# читает конвертер PDF→PNG между страницами (services/cache/cache_watch.py)
PNG_WATCH_FILENAME: str = "_watch.json"

# Маркер общего числа страниц PDF в PNG-директории.
# Пишется pre-scan'ом до рендеринга: вьюер рисует заглушки, а /pages отличает частичную директорию
PNG_PAGES_TOTAL_FILENAME: str = "_pages_total.txt"

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

# Конвертер PDF→PNG встаёт на паузу, если heartbeat просмотра не обновлялся дольше этого (секунды).
# Отсчёт — от max(старт запуска, последний heartbeat): запуск без зрителя рендерит не дольше этого окна.
# Окно с запасом перекрывает период опроса png-viewer (2 с) и троттлинг таймеров фоновой вкладки (1 с)
PDF_CONVERT_IDLE_TIMEOUT_SECONDS: int = 20


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

# Статус записи PDF в _meta.json: конвертация на паузе, готовы не все страницы (pages_done < pages).
# Кеш при этом валиден: докрутка — resume_pdf_conversion при следующем просмотре
CACHE_FILE_STATUS_PARTIAL: str = "partial"

# Стадии подготовки кеша
CACHE_STAGE_STARTING: str = "starting"
CACHE_STAGE_EXTRACTING: str = "extracting"
CACHE_STAGE_CONVERTING: str = "converting"

# Допустимые kind у POST /api/cache/{name}/resolve.
# PDF-вьюер ходит в /api/png/.../pages напрямую, поэтому "pdf" здесь нет:
# неизвестный kind иначе провалился бы в авто-запуск подготовки ZIP.
CACHE_RESOLVE_KINDS: frozenset = frozenset({"image", "track", "all_tracks"})
