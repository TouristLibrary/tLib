# Version 1.4 - 21.06.2026 13:00:00 GMT
# Конфигурация безопасности TlibWebApp
# Описание: CSP политики, заголовки безопасности, HTTP методы, rate limiting,
#           паттерны детекции атак и ограничения размеров файлов.
# Изменения v1.1: добавлены AUTH_CODE_LENGTH, AUTH_CODE_MAX_ATTEMPTS для гибридной авторизации.
# Изменения v1.2: X-XSS-Protection -> 0 (легаси-аудитор отключён, защита через CSP);
#                 AUTH_COOKIE_SECURE по умолчанию true (fail-secure).
# Изменения v1.3: AUTH_EMAIL_DAILY_CAP, AUTH_REQUEST_LINK_IP_MAX, AUTH_REQUEST_LINK_IP_WINDOW
#                 для анти-абуз системы request-link (дневной лимит + пер-IP троттлинг).
# Изменения v1.4: UPLOAD_READ_CHUNK_SIZE — размер чанка потоковой записи upload-файлов;
#                 UPLOAD_DISK_RESERVE_MULTIPLIER — резерв свободного места для блокировки загрузок.

# ==================== RATE LIMITING ====================

# Максимальное количество запросов с одного IP адреса в минуту
# Защита от DoS атак и брутфорса
RATE_LIMIT_REQUESTS_PER_MINUTE: int = 300

# Интервал очистки старых записей в Rate Limiter (в секундах)
RATE_LIMIT_CLEANUP_INTERVAL: int = 300  # 5 минут

# Окно времени для подсчета запросов (в секундах)
RATE_LIMIT_WINDOW_SECONDS: int = 60

# Порог количества запросов к статическим файлам для логирования предупреждения
# Превышение этого значения не блокирует запросы, но логируется как подозрительная активность
RATE_LIMIT_STATIC_WARNING_THRESHOLD: int = 1000

# Жёсткий лимит для статических файлов (блокировка при превышении)
# Высокий порог чтобы не мешать легитимным PDF Range-запросам
RATE_LIMIT_STATIC_HARD_THRESHOLD: int = 5000

# Максимум одновременных запросов к статике с одного IP
# Защита от параллельных массовых скачиваний
MAX_CONCURRENT_STATIC_CONNECTIONS: int = 10

# Retry-After для ответа 429 при concurrent-лимите (секунды)
RETRY_AFTER_CONCURRENT_SECONDS: int = 10

# Список путей к "легким" статическим файлам (без ограничения concurrent connections)
# Для этих файлов применяется только жёсткий лимит запросов, без ограничения параллельных соединений
# Позволяет ES модулям и CSS загружаться параллельно при сохранении защиты от DDoS
RATE_LIMIT_LIGHT_STATIC_PATHS: list[str] = ['/js/', '/css/']


# ==================== КОНСТАНТЫ БЕЗОПАСНОСТИ ====================

# Максимальный размер ZIP архива для обработки (в байтах)
# Архивы больше этого размера не будут обрабатываться для защиты от DoS атак
MAX_ARCHIVE_SIZE: int = 2 * 1024 * 1024 * 1024  # 2 ГБ

# Максимальный размер одного файла внутри архива (в байтах)
# Файлы больше этого размера будут пропущены при обработке
MAX_FILE_SIZE: int = 2 * 1024 * 1024 * 1024  # 2 ГБ

# Максимальное количество файлов в одном архиве
# Архивы с большим количеством файлов не будут обрабатываться для защиты от DoS атак
MAX_FILES_IN_ARCHIVE: int = 1000

# Максимальное соотношение распакованного к сжатому размеру (compression ratio)
# Защита от Zip Bomb атак (обычные архивы: 2-15x, Zip Bomb: 1000x+)
MAX_COMPRESSION_RATIO: int = 100

# Максимальное количество раундов URL-декодирования (защита от multi-encoding атак)
# Декодирование выполняется ограниченное число раз для защиты от double/triple encoding
URL_DECODE_MAX_ROUNDS: int = 2

# Размер чанка при потоковой записи загружаемых файлов на диск (в байтах).
# Пиковое потребление RAM на один upload-запрос ≈ UPLOAD_READ_CHUNK_SIZE.
UPLOAD_READ_CHUNK_SIZE: int = 1024 * 1024  # 1 МБ

# Кратный запас свободного места, требуемый для приёма загрузки.
# Загрузки блокируются при free_bytes < MAX_ARCHIVE_SIZE * UPLOAD_DISK_RESERVE_MULTIPLIER.
UPLOAD_DISK_RESERVE_MULTIPLIER: int = 2

# Белый список IP для исключения из детекции атак (healthcheck, мониторинг)
# Эти IP адреса не считаются подозрительными при запросах с CLI инструментами
SECURITY_WHITELIST_IPS: list[str] = ['127.0.0.1', '::1']

# CLI инструменты для детекции подозрительных запросов
# Используется в path_security для выявления автоматических сканеров
SECURITY_CLI_TOOLS: list[str] = ['curl', 'wget', 'python', 'go-http']

# URL-encoded паттерны Path Traversal (детектируются ДО декодирования)
SECURITY_URL_ENCODED_PATTERNS: list[str] = [
    '%2e%2e/',       # URL-encoded ../
    '%2e%2e%2f',     # URL-encoded ../ (строчные)
    '%2e%2e\\',      # URL-encoded ..\
    '%2e%2e%5c',     # URL-encoded ..\ (строчные)
    '%252e%252e',    # Double URL-encoded ..
    '%2e%2e%2e',     # Triple dots encoded
]

# Декодированные паттерны Path Traversal (детектируются после декодирования Uvicorn)
SECURITY_DECODED_PATTERNS: list[str] = [
    '/../',          # Path traversal между слешами
    '/..\\',         # Windows path traversal между слешами
]

# Подозрительные пути для мониторинга 404 (результат нормализации атак)
SECURITY_SUSPICIOUS_PATHS: list[str] = [
    '/etc/',         # Системные файлы Unix
    '/root/',        # Root домашняя директория
    '/proc/',        # Процессы Unix
    '/sys/',         # Системная информация Unix
    '/secret',       # Часто используется в тестах атак
    '/admin',        # Административные пути
    '/config',       # Конфигурационные файлы
    '/passwd',       # Файл паролей
    '/shadow',       # Shadow файл паролей
    '/.env',         # Environment переменные
    '/.git/',        # Git репозиторий
    '/var/log/',     # Логи системы
    '/tmp/',         # Временные файлы
]


# ==================== ПОЛИТИКИ БЕЗОПАСНОСТИ ====================

# Строгий CSP для опасных файлов — блокирует скрипты, разрешает отображение
# Применяется к файлам из DANGEROUS_INLINE_EXTENSIONS при отдаче из архивов
# Защищает от XSS через загруженный контент, сохраняя возможность просмотра
STRICT_FILE_CSP: str = (
    "default-src 'none'; "        # Запретить всё по умолчанию
    "img-src 'self' data:; "      # Разрешить картинки для отображения HTML/SVG
    "style-src 'unsafe-inline'; " # Разрешить inline-стили для отображения
    "font-src 'self' data:"       # Разрешить шрифты
)

# Content Security Policy (CSP) - политика безопасности содержимого
# Определяет, какие ресурсы могут быть загружены и выполнены на странице
CSP_POLICY: str = (
    "default-src 'self'; "                        # По умолчанию загружать только с того же домена
    "script-src 'self' 'wasm-unsafe-eval'; "      # Скрипты: свой домен + WebAssembly
    "style-src 'self' 'unsafe-inline'; "          # Стили: свой домен + inline стили
    "img-src 'self' data:; "                      # Изображения: свой домен + data: URI
    "font-src 'self' data:; "                     # Шрифты: свой домен + data: URI
    "connect-src 'self' blob:; "                  # AJAX/WebSocket: свой домен + blob: URI
    "worker-src blob: 'self'; "                   # Web Workers: blob: + свой домен
    "frame-src 'self' blob: https://nakarte.me; " # Разрешить iframe с того же домена, blob: URL и nakarte.me
    "object-src 'self'; "                         # Разрешить embed/object с того же домена (для PDF)
    "child-src blob: 'self'; "                    # Разрешить child frames
    "frame-ancestors 'self'; "                    # Разрешить встраивание на страницах того же домена
    "base-uri 'self'; "                           # Ограничить base URL
    "form-action 'self'"                          # Отправка форм только на свой домен
)

# Словарь дополнительных заголовков безопасности
# Эти заголовки защищают от различных типов атак
SECURITY_HEADERS: dict[str, str] = {
    # Запретить браузеру угадывать MIME тип (защита от MIME sniffing атак)
    "X-Content-Type-Options": "nosniff",

    # Разрешить отображение в iframe только на страницах того же домена (защита от clickjacking)
    "X-Frame-Options": "SAMEORIGIN",

    # Легаси-аудитор отключён: значение "0" рекомендовано OWASP вместо "1; mode=block",
    # так как аудитор убран из всех современных браузеров; защита — только через CSP.
    "X-XSS-Protection": "0",

    # Политика отправки referrer (не отправлять при переходе на HTTP)
    "Referrer-Policy": "strict-origin-when-cross-origin",

    # Принудительное использование HTTPS (для HTTPS: Tailscale Funnel / Caddy)
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",

    # Запретить Adobe Flash/PDF cross-domain policy файлы (защита от legacy атак)
    "X-Permitted-Cross-Domain-Policies": "none"
}

# Заголовок Cache-Control для HTML страниц
# Заставляет браузер всегда проверять актуальность страницы на сервере
CACHE_CONTROL_HTML: str = "no-cache, must-revalidate"

# Cache-Control заголовок для revalidate-кеширования статических файлов
# Позволяет браузеру кешировать файлы с обязательной валидацией (304 Not Modified)
# Используется для файлов в /data/ для ускорения повторных открытий
CACHE_CONTROL_REVALIDATE: str = "private, no-cache, must-revalidate"

# Cache-Control для API ответов со справочными данными (версии, списки)
# no-store — не кешировать, всегда запрашивать свежие данные
CACHE_CONTROL_NO_STORE: str = "no-store"

# Cache-Control для JS/CSS статических файлов
# no-cache — браузер кеширует файл, но при каждом использовании делает conditional request (If-Modified-Since)
# Сервер отвечает 304 без тела, если файл не менялся — актуальность без повторной загрузки
CACHE_CONTROL_STATIC: str = "no-cache"


# ==================== HTTP МЕТОДЫ ====================

# Разрешенные HTTP методы
# Блокируются потенциально опасные методы (PUT, DELETE, PATCH)
ALLOWED_HTTP_METHODS: set[str] = {"GET", "HEAD", "OPTIONS", "POST"}

# Префикс API для POST запросов
# POST разрешен только для путей, начинающихся с этого префикса
API_PATH_PREFIX: str = "/api/"

# Расширения файлов, требующие строгий CSP (могут содержать скрипты)
# Эти файлы могут содержать активный контент (JavaScript, SVG скрипты, XSLT)
# При отдаче таких файлов применяется STRICT_FILE_CSP вместо стандартного CSP_POLICY
DANGEROUS_INLINE_EXTENSIONS: set[str] = {'.html', '.htm', '.xml', '.svg'}


# ==================== AUTH ====================

import os

# Имя cookie сессии пользователя
AUTH_COOKIE_NAME: str = "session_token"

# Время жизни cookie и сессии (секунды). По умолчанию 30 дней
AUTH_SESSION_MAX_AGE: int = 30 * 24 * 3600

# Время жизни magic link (секунды). По умолчанию 15 минут
AUTH_MAGIC_LINK_TTL: int = 15 * 60

# Минимальный интервал между запросами magic link для одного email (секунды)
AUTH_MAGIC_LINK_RATE: int = 60

# Длина цифрового кода авторизации
AUTH_CODE_LENGTH: int = 6

# Максимальное количество неверных попыток ввода кода; при превышении — код аннулируется
AUTH_CODE_MAX_ATTEMPTS: int = 5

# Флаг Secure для session-cookie. По умолчанию True (fail-secure): cookie отправляется
# только по HTTPS. Локальный HTTP-режим: явно задать AUTH_COOKIE_SECURE=false в data.secret/.env.
AUTH_COOKIE_SECURE: bool = os.environ.get("AUTH_COOKIE_SECURE", "true").strip().lower() in ("1", "true", "yes")

# Дневной лимит исходящих писем для request-link (ниже квоты Gmail ~500/сутки).
# Защита от злоупотребления: рассылка на чужие адреса и/или исчерпание SMTP-квоты.
# При исчерпании → 429 + security WARNING + EMAIL_QUOTA алерт администратору.
AUTH_EMAIL_DAILY_CAP: int = 400

# Пер-IP троттлинг для POST /api/auth/request-link (в памяти процесса).
# Не более AUTH_REQUEST_LINK_IP_MAX запросов в окне AUTH_REQUEST_LINK_IP_WINDOW секунд с одного IP.
# Эффективен при условии корректного проброса X-Forwarded-For за reverse-proxy (п.2).
AUTH_REQUEST_LINK_IP_MAX: int = 5
AUTH_REQUEST_LINK_IP_WINDOW: int = 600  # 10 минут

# SMTP — секреты читаются из data.secret/.env через python-dotenv
# Если .env отсутствует, значения остаются пустыми строками:
# сайт работает нормально, auth выдаёт ошибку отправки письма
SMTP_SERVER: str   = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT: int     = int(os.environ.get("SMTP_PORT", "587"))
SMTP_SENDER: str   = os.environ.get("SMTP_SENDER", "")
SMTP_PASSWORD: str = os.environ.get("SMTP_PASSWORD", "")
SITE_URL: str      = os.environ.get("SITE_URL", "")


# ==================== ADMIN PANEL ====================

# Email суперадмина — всегда имеет права админа, нельзя отобрать через UI
# Читается из data.secret/.env через python-dotenv
ROOT_ADMIN_EMAIL: str = os.environ.get("ROOT_ADMIN_EMAIL", "").strip().lower()
