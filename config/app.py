# Version 1.2 - 16.06.2026 22:00:00 GMT
# Конфигурация приложения TlibWebApp
# Описание: Метаданные, сетевые настройки, пути к ресурсам, логирование,
#           редиректы и ключи app.state.
# 1.2: добавлены OG_IMAGE_PATH/OG_IMAGE_WIDTH/OG_IMAGE_HEIGHT (Open Graph картинка).
# 1.1: добавлены REDIRECT_SOURCE_ALIASES, REDIRECT_DEFAULT_ASPX_PATHS (legacy IIS),
#      ROBOTS_CLEAN_PARAMS (Яндекс Clean-param для UTM/openstat меток).

# ==================== МЕТАДАННЫЕ ПРИЛОЖЕНИЯ ====================

# Метаданные FastAPI приложения
APP_TITLE: str = "Archive Viewer"
APP_DESCRIPTION: str = "Archive viewing service"
APP_VERSION: str = "1.0.0"


# ==================== СЕТЕВЫЕ НАСТРОЙКИ ====================

# Порт по умолчанию для запуска FastAPI сервера
# Может быть переопределен через переменную окружения PORT
DEFAULT_PORT: int = 8080

# Хост для прослушивания входящих соединений
# "0.0.0.0" означает прослушивание на всех доступных сетевых интерфейсах
DEFAULT_HOST: str = "0.0.0.0"


# ==================== ПУТИ К РЕСУРСАМ ====================

# URL путь к локальным архивам (PDF, ZIP файлы)
# Используется для формирования ссылок на скачивание в браузере
LOCAL_ARCHIVE_PATH: str = "/data"

# Директория с архивами на диске
# Может быть абсолютным путем (USB) или относительным (локально/симлинк)
DATA_DIRECTORY: str = "data"

# Директория для кэширования GPS-треков
# Хранит созданные архивы формата {имя_архива}-geo.zip
CACHE_DIRECTORY: str = "data.cache"

# URL путь к директории кеша (PNG pages, извлечённые файлы)
# Используется для монтирования StaticFiles и формирования URL в PNG viewer
CACHE_URL_PATH: str = "/cache"

# Список статических директорий, которые будут обслуживаться FastAPI
# Каждая директория монтируется как отдельный статический маршрут
# ПРИМЕЧАНИЕ: data/ монтируется отдельно в app.py для поддержки гибких путей
STATIC_DIRS: list[str] = ['js', 'css', 'assets', 'data.db']

# Путь к иконке сайта (favicon)
FAVICON_PATH: str = "assets/favicon.ico"

# URL путь к favicon для исключения из rate limiting
FAVICON_URL_PATH: str = "/favicon.ico"

# Open Graph картинка: URL-путь, ширина и высота в пикселях
OG_IMAGE_PATH: str = "/assets/og-image.png"
OG_IMAGE_WIDTH: int = 1200
OG_IMAGE_HEIGHT: int = 630


# ==================== ЛОГИРОВАНИЕ ====================

# Директория для файлов логов
LOG_DIRECTORY: str = "logs"

# Имена файлов логов
LOG_FILE_APP: str = "app.log"
LOG_FILE_DEBUG: str = "debug.log"
LOG_FILE_CRITICAL: str = "critical.log"

# Длина короткого request_id для отображения в логах (полный UUID сохраняется)
REQUEST_ID_SHORT_LENGTH: int = 8

# Уровень логирования для консоли (DEBUG, INFO, WARNING, ERROR, CRITICAL)
LOG_CONSOLE_LEVEL: str = "WARNING"

# Уровень логирования для debug.log (DEBUG, INFO, WARNING, ERROR, CRITICAL, OFF)
# OFF - полностью отключает debug.log (рекомендуется для production)
LOG_DEBUG_LEVEL: str = "OFF"

# Максимальные размеры файлов логов (в байтах)
# При достижении размера файл автоматически ротируется
LOG_APP_MAX_SIZE: int = 20 * 1024 * 1024  # 20MB - основные события (INFO+)
LOG_DEBUG_MAX_SIZE: int = 50 * 1024 * 1024  # 50MB - отладочная информация (DEBUG+)
LOG_CRITICAL_MAX_SIZE: int = 10 * 1024 * 1024  # 10MB - критические события (WARNING+)

# Количество резервных копий файлов логов
# Старые файлы сохраняются с суффиксами .1, .2, .3 и т.д.
LOG_APP_BACKUP_COUNT: int = 5  # Хранить 5 копий app.log
LOG_DEBUG_BACKUP_COUNT: int = 3  # Хранить 3 копии debug.log
LOG_CRITICAL_BACKUP_COUNT: int = 10  # Хранить 10 копий critical.log (важно для администратора)


# ==================== РЕДИРЕКТЫ ====================

# Маршрут для редиректа (старый URL)
REDIRECT_SOURCE: str = "/doc.aspx"

# Альтернативные регистровые варианты /doc.aspx (IIS нечувствителен к регистру)
REDIRECT_SOURCE_ALIASES: list[str] = ["/Doc.aspx", "/DOC.ASPX", "/Doc.ASPX", "/DOC.aspx"]

# Пути главной страницы старого сайта (ASP.NET default document)
REDIRECT_DEFAULT_ASPX_PATHS: list[str] = ["/default.aspx", "/Default.aspx", "/DEFAULT.ASPX"]

# Код статуса для редиректа (302 = временный редирект)
REDIRECT_STATUS_CODE: int = 302

# Код статуса для редиректа статических директорий (301 = постоянный редирект)
STATIC_REDIRECT_STATUS_CODE: int = 301

# Код статуса для legacy редиректов (301 = постоянный редирект)
# Используется для редиректов с маппингом по таблице redirect_table
LEGACY_REDIRECT_STATUS_CODE: int = 301

# Clean-param директива для robots.txt — Яндекс использует её для склейки дублей,
# возникающих из-за UTM-меток и параметров аналитики в URL
ROBOTS_CLEAN_PARAMS: str = (
    "utm_source&utm_medium&utm_campaign&utm_term&utm_content"
    "&utm_expid&openstat&yclid&from&gclid"
)


# ==================== КЛЮЧИ APP.STATE ====================

# Ключи для доступа к инфраструктурным и справочным атрибутам app.state
# Используются в hasattr/getattr в app.py, health_router.py и сервисах
STATE_REFERENCE_VERSION: str = 'reference_version'
STATE_DB_WATCHER_TASK: str = 'db_watcher_task'
STATE_FILE_WATCHER_TASK: str = 'file_watcher_task'
STATE_STATS_FLUSH_TASK: str = 'stats_flush_task'
STATE_KATEGORIA_UNIFIED: str = 'kategoria_unified_list'
STATE_REPORTS_COUNT: str = 'reports_count'
STATE_STARTED_AT: str = 'started_at'
