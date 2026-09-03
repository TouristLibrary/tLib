# Version 1.0 - 21.02.2026 00:00:00 GMT
# Конфигурация медиа-типов TlibWebApp
# Описание: MIME типы, расширения файлов, GPS треки, настройки конвертации
#           изображений (JPG) и PDF (PNG), кодировки ZIP архивов.

# ==================== MIME ТИПЫ И INLINE РАСШИРЕНИЯ ====================

# Словарь соответствия расширений файлов и MIME типов
# Используется для правильной отдачи файлов браузеру
MIME_TYPES: dict[str, str] = {
    # JavaScript и CSS
    '.js': 'application/javascript',
    '.css': 'text/css',
    '.json': 'application/json',

    # Изображения
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.svg': 'image/svg+xml',
    '.webp': 'image/webp',
    '.bmp': 'image/bmp',
    '.ico': 'image/x-icon',
    '.avif': 'image/avif',

    # Документы
    '.pdf': 'application/pdf',
    '.doc': 'application/msword',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xls': 'application/vnd.ms-excel',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.txt': 'text/plain',
    '.html': 'text/html',
    '.htm': 'text/html',
    '.xml': 'application/xml',

    # GPS и карты
    '.gpx': 'application/gpx+xml',
    '.kml': 'application/vnd.google-earth.kml+xml',

    # Архивы
    '.zip': 'application/zip',
    '.rar': 'application/x-rar-compressed',
    '.7z': 'application/x-7z-compressed'
}

# MIME тип по умолчанию для файлов с неизвестным расширением
DEFAULT_MIME_TYPE: str = "application/octet-stream"

# Расширение файла по умолчанию (fallback для Content-Disposition)
DEFAULT_FILE_EXTENSION: str = ".bin"


# ==================== КАТЕГОРИИ РАСШИРЕНИЙ ФАЙЛОВ ====================
# Атомарные наборы — каждое расширение принадлежит ровно одной категории.

# Растровые изображения (кандидаты на конвертацию в JPG)
RASTER_IMAGE_EXTENSIONS: set[str] = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.avif'}

# Векторные/специальные изображения (без конвертации)
VECTOR_IMAGE_EXTENSIONS: set[str] = {'.svg', '.ico'}

# Все изображения (производный набор)
ALL_IMAGE_EXTENSIONS: set[str] = RASTER_IMAGE_EXTENSIONS | VECTOR_IMAGE_EXTENSIONS

# Список расширений файлов, которые должны отображаться inline (в браузере)
# Остальные файлы будут скачиваться с заголовком Content-Disposition: attachment
INLINE_EXTENSIONS: list[str] = [
    '.pdf',      # PDF документы
    '.jpg',      # Изображения JPEG
    '.jpeg',     # Изображения JPEG
    '.png',      # Изображения PNG
    '.gif',      # Анимированные изображения GIF
    '.svg',      # Векторные изображения SVG
    '.webp',     # Изображения WebP
    '.bmp',      # Изображения BMP
    '.ico',      # Иконки
    '.avif',     # Изображения AVIF (современные браузеры)
    '.txt',      # Текстовые файлы
    '.html',     # HTML документы
    '.htm',      # HTML документы
    '.xml',      # XML документы
    '.json'      # JSON данные
]


# ==================== GPS ТРЕКИ ====================

# Расширения файлов GPS-треков для создания архива всех треков
GPS_TRACK_EXTENSIONS: set[str] = {'.gpx', '.kml', '.kmz', '.plt', '.rte', '.wpt', '.geojson'}

# Минимальное количество треков для создания архива
MIN_TRACKS_FOR_ARCHIVE: int = 2


# ==================== IMAGE TO JPG CONVERSION ====================

# Расширения изображений для оптимизации
# Используется в image_conversion_service.py и cache_pipeline.py
# Только растровые изображения (векторные и документы исключены)
IMAGE_EXTENSIONS: set[str] = RASTER_IMAGE_EXTENSIONS

# Форматы, которые PyMuPDF может сохранять нативно (без Pillow)
# PNG оптимизируется в своём формате, остальные конвертируются в JPG
FITZ_NATIVE_IMAGE_FORMATS: set[str] = {'.png'}

# Автоматическая оптимизация изображений (ресайз / копия без перекодирования)
# Все растры проходят через оптимизатор:
# - Если пиксельные размеры в пределах лимитов — файл копируется без перекодирования
# - Если нужен ресайз — уменьшается с сохранением пропорций:
#     PNG → PNG, остальные форматы → JPEG
# True  — включить оптимизацию (требует PyMuPDF: pip install pymupdf)
# False — отключить оптимизацию
IMAGE_TO_JPG_ENABLED: bool = True

# Максимальные размеры оптимизированных изображений (пиксели)
# При превышении хотя бы одного размера — ресайз с сохранением пропорций
# PNG сохраняется как PNG, остальные форматы конвертируются в JPG
IMAGE_TO_JPG_MAX_WIDTH: int = 1280
IMAGE_TO_JPG_MAX_HEIGHT: int = 960

# Качество JPEG сжатия (1-100)
# 85 — оптимальное соотношение качество/размер
# 95 — высокое качество (больше размер)
# 75 — экономичное (меньше размер)
IMAGE_TO_JPG_QUALITY: int = 85


# ==================== PDF TO PNG CONVERSION ====================

# Автоматическая конвертация PDF в директории с PNG страницами
# При попадании PDF в data.cache/ автоматически создается директория с PNG
# True  — конвертировать (требует PyMuPDF: pip install pymupdf)
# False — не конвертировать (текущее поведение)
PDF_TO_PNG_ENABLED: bool = True

# Разрешение рендеринга (DPI). Рекомендуемые значения:
# 72 DPI - экранное качество (быстро, маленький размер)
# 96 DPI - стандартное качество для веб (оптимально)
# 150 DPI - хорошее качество для детального просмотра
# 300 DPI - качество печати (медленно, большой размер)
PDF_TO_PNG_DPI: int = 96

# Цветовое пространство: "rgb" (цветной) или "gray" (ч/б, меньше размер)
PDF_TO_PNG_COLORSPACE: str = "rgb"

# Альфа-канал (прозрачность). False = белый фон (рекомендуется)
PDF_TO_PNG_ALPHA: bool = False


# ==================== КОДИРОВКИ ZIP ====================

# Список кодировок для попытки декодирования имен файлов в ZIP архивах
# Порядок важен: сначала пробуются более вероятные кодировки
# Используется для корректного отображения кириллических имен файлов
# CP866 (DOS) проверяется перед CP1251 (Windows), так как многие старые архивы
# созданы в DOS/Windows 9x и используют CP866 для кириллицы
ZIP_ENCODINGS: list[str] = [
    'utf-8',    # Современная универсальная кодировка (macOS, Linux, современный Windows)
    'cp866',    # DOS кириллица (старые архивы, WinRAR, 7-Zip в режиме DOS)
    'cp1251',   # Windows кириллица (Windows XP+)
    'latin1'    # Резервная кодировка (ISO-8859-1)
]

# Фильтровать служебные файлы macOS из списков архивов
# macOS автоматически создает папку __MACOSX/ и файлы с префиксом ._
# которые содержат метаданные и расширенные атрибуты (resource fork)
# Эти файлы не нужны пользователям и скрываются при отображении содержимого архива
FILTER_MACOS_METADATA: bool = True
