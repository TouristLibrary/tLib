# Version 1.8 - 07.10.2026 06:08:09 GMT
# Конфигурация кеша TlibWebApp
# Описание: Имена файлов и директорий кеша, статусы, стадии подготовки,
#           таймауты и параметры LRU-очистки.
# 1.1: CACHE_RESOLVE_KINDS — допустимые kind у /api/cache/{name}/resolve (pdf убран).
# 1.2: конвертация PDF, пока смотрят — PNG_WATCH_FILENAME, PNG_PAGES_TOTAL_FILENAME,
#      PDF_CONVERT_IDLE_TIMEOUT_SECONDS, CACHE_FILE_STATUS_PARTIAL.
# 1.3: рендер PDF по окну просмотра — PDF_CONVERT_PREWARM_PAGES, PDF_CONVERT_LOOKAHEAD_PAGES;
#      PDF_CONVERT_IDLE_TIMEOUT_SECONDS — только критерий свежести heartbeat.
# 1.4: удалены CACHE_ZIP_SIZE_MULTIPLIER и CACHE_PDF_SIZE_MULTIPLIER — место освобождается
#      по фактическому размеру папки после записи _meta.json, а не по оценке.
# 1.5: комментарии к heartbeat и окну — опрос раз в 1–2 с, дозаполнение позади want (значения прежние).
# 1.6: PDF_CONVERT_BACKFILL_FRESH_SECONDS — дозаполнение позади want только при свежем (≤ 5 с) heartbeat.
# 1.7: long-poll /pages — PNG_PAGES_WAIT_SECONDS, PNG_PAGES_WAIT_STEP_SECONDS; соседи want рендерятся
#      вперемешку с обеих сторон — PDF_CONVERT_NEAR_PAGES.
# 1.8: комментарии к окну и дозаполнению — вьюер опрашивает /pages, пока сервер рисует для want
#      (rendering в ответе), иначе — при смене страницы (значения прежние).

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
# Пишет GET /api/png/.../pages (png-viewer опрашивает его, пока на диске не все страницы),
# читает конвертер PDF→PNG между страницами (services/cache/cache_watch.py)
PNG_WATCH_FILENAME: str = "_watch.json"

# Маркер общего числа страниц PDF в PNG-директории.
# Пишется pre-scan'ом до рендеринга: вьюер рисует заглушки, а /pages отличает частичную директорию
PNG_PAGES_TOTAL_FILENAME: str = "_pages_total.txt"

# Суффикс имени файла GPS-архива (добавляется к имени архива)
# Результат: {archive_name}-geo.zip
GEO_ARCHIVE_SUFFIX: str = "-geo.zip"

# ==================== РАЗМЕРЫ И ТАЙМАУТЫ КЕША ====================

# Максимальный размер всего кеша data.cache/ (GPS архивы + извлеченные файлы).
# Место освобождается после записи _meta.json, по фактическому размеру папки, поэтому
# на диске нужен запас сверх лимита на один запуск: распакованный ZIP (файлы до MAX_FILE_SIZE)
# плюс его выход — несколько ГБ
MAX_CACHE_SIZE: int = 150 * 1024 * 1024 * 1024  # 150 ГБ

# Допуск при сравнении mtime файлов (секунды)
# Файловые системы могут округлять время модификации
MTIME_TOLERANCE: float = 0.1

# Таймаут для протухшей блокировки кеша (минуты)
# Если heartbeat не обновлялся дольше этого — lock считается stale
CACHE_STALE_LOCK_TIMEOUT_MINUTES: int = 5

# Retry-After для клиента при подготовке кеша (миллисекунды)
CACHE_RETRY_AFTER_MS: int = 1000

# Heartbeat просмотра старше этого (секунды) — зрителя нет: конвертер PDF→PNG рендерит только
# прогрев (PDF_CONVERT_PREWARM_PAGES). Критерий свежести, а не бюджет времени рендера.
# С запасом перекрывает период опроса png-viewer (2 с) и троттлинг таймеров фоновой вкладки (1 с)
PDF_CONVERT_IDLE_TIMEOUT_SECONDS: int = 20

# Прогрев без зрителя: столько первых страниц PDF рендерится по жесту до открытия вкладки —
# человек сразу видит начало документа, а бот, прошедший гейт, не рендерит весь PDF
PDF_CONVERT_PREWARM_PAGES: int = 5

# При свежем heartbeat конвертер рендерит не дальше стольких страниц вперёд от просматриваемой (want).
# Рендер ~3 стр/с; вьюер шлёт запрос при смене страницы и опрашивает раз в 1–2 с, пока сервер рисует:
# живой читатель за окно не выходит и заглушек не видит.
# Окно готово — дозаполняются страницы позади want, пока heartbeat не старше
# PDF_CONVERT_BACKFILL_FRESH_SECONDS. Докрутка запускается, только когда впереди от want готово
# меньше половины окна или позади want есть дырки (гистерезис)
PDF_CONVERT_LOOKAHEAD_PAGES: int = 20

# Дозаполнение страниц позади want — только при heartbeat не старше этого (секунды); окно вперёд
# живёт по PDF_CONVERT_IDLE_TIMEOUT_SECONDS. Пока позади want есть дырки, сервер отвечает
# rendering=true и вьюер опрашивает /pages раз в 2 с — под порог попадает всегда; бот, ушедший
# с deep-link, дозаполняет не дольше 5 с (≈15 страниц),
# а не все 20 с IDLE_TIMEOUT. Фоновая вкладка с сильным троттлингом таймеров (после 5 минут скрытия)
# под порог не попадает — и это нормально: там не читают
PDF_CONVERT_BACKFILL_FRESH_SECONDS: int = 5

# Соседи want при свежем (≤ PDF_CONVERT_BACKFILL_FRESH_SECONDS) heartbeat рендерятся вперемешку:
# want, want+1, want-1, want+2, want-2 … — столько пар, затем остаток окна вперёд и страницы позади.
# Читатель, открывший deep-link на середину, чаще листает на шаг назад, чем на 20 вперёд.
# 0 — прежний порядок: всё окно вперёд, потом назад
PDF_CONVERT_NEAR_PAGES: int = 3

# Long-poll GET /api/png/.../pages: страницы want нет на диске частичной директории — запрос ждёт
# её появления не дольше стольких секунд и отвечает сразу, как файл появился. Читатель видит
# страницу в момент рендера, а не на следующем опросе. 0 — ответ без ожидания (прежнее поведение)
PNG_PAGES_WAIT_SECONDS: float = 1.5

# Шаг проверки файла страницы при long-poll /pages (секунды): задержка показа сверх рендера
PNG_PAGES_WAIT_STEP_SECONDS: float = 0.1


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
