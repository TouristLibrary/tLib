# Архитектура TlibWebApp

Веб-приложение для просмотра архивов туристских отчетов с поиском по базе данных SQLite.

## Основные возможности

- Полнотекстовый и параметрический поиск в SQLite
- Онлайн просмотр ZIP архивов без распаковки
- Встроенный просмотрщик PDF в браузере
- Поддержка GPS-треков (GPX, KML, KMZ, GeoJSON)
- Автоматическая обработка загружаемых файлов (File Watcher)
- Удаление отчетов через .delete триггеры с бэкапом
- Защита от атак, rate limiting, мониторинг

---

## Компоненты системы

```
┌─────────────────────────────────────────────────────────────┐
│                        Browser                               │
└─────────────────────────┬───────────────────────────────────┘
                          │ HTTP
┌─────────────────────────▼───────────────────────────────────┐
│                    uvicorn (ASGI Server)                     │
│                      Port 8080                               │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                    FastAPI Application                       │
│  ┌───────────────┐ ┌──────────────┐ ┌─────────────────────┐ │
│  │  Middlewares  │ │   Routers    │ │     Services        │ │
│  │ - Rate Limit  │ │ - search     │ │ - archive_service   │ │
│  │ - Path Secur. │ │ - archive    │ │ - file_service      │ │
│  │ - Headers     │ │ - lists      │ │ - database/         │ │
│  │ - HTTP Methods│ │ - config     │ │ - file_watcher/     │ │
│  │ - Request ID  │ │ - cache      │ │ - cache/            │ │
│  │ - Data Downl. │ │ - pdf        │ │ - conversion/       │ │
│  │ - Traffic Stat│ │ - png_viewer │ │ - validation/       │ │
│  │               │ │ - admin      │ │ - security/         │ │
│  │               │ │ - health     │ │ - upload/           │ │
│  │               │ │ - static     │ │ - alerts/           │ │
│  └───────────────┘ └──────────────┘ └─────────────────────┘ │
└─────────────────────────┬───────────────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┬─────────────────┐
        ▼                 ▼                 ▼                 ▼
┌───────────────┐  ┌────────────┐  ┌───────────────┐  ┌───────────────┐
│  data.db/     │  │   data/    │  │   data.up/    │  │  data.cache/  │
│  tlib.db      │  │  archives  │  │   uploads     │  │  файловый кеш │
│  (SQLite)     │  │  (ZIP/PDF) │  │   (pipeline)  │  │  (LRU)        │
└───────────────┘  └────────────┘  └───────────────┘  └───────────────┘
```

---

## Структура проекта

```
TlibWebApp/
├── app.py                  # Точка входа, регистрация компонентов
├── logging_config.py       # Система логирования
│
├── config/                 # Конфигурация (пакет)
│   ├── app.py              # Метаданные, сеть, пути, логирование, редиректы
│   ├── database.py         # БД, бэкапы, File Watcher, тяжёлые запросы, upload-константы
│   ├── security.py         # CSP, заголовки, rate limiting, детекция атак
│   ├── cache.py            # Статусы, стадии, таймауты кеша
│   ├── media.py            # MIME типы, GPS, конвертация изображений и PDF
│   ├── fields.py           # Справочные поля, поля поиска
│   └── alerts.py           # Уровни, пороги и константы email-уведомлений
│
├── middlewares/            # HTTP Middleware
│   ├── rate_limit.py       # Ограничение запросов
│   ├── path_security.py    # Детекция Path Traversal атак
│   ├── security_headers.py # Заголовки безопасности
│   ├── http_methods.py     # Фильтрация методов
│   ├── request_id.py       # Уникальный ID запроса
│   ├── data_download.py    # Content-Disposition для файлов /data/
│   └── traffic_stats.py    # Статистика посещений → stats.db (data.secret/)
│
├── routers/                # API Endpoints (тонкий HTTP-слой: auth + вызов сервиса)
│   ├── search_router.py    # POST /api/search
│   ├── archive_router.py   # GET /api/archive/*
│   ├── lists_router.py     # GET /api/*-list
│   ├── config_router.py    # GET /api/config
│   ├── cache_router.py     # POST /api/cache/*
│   ├── pdf_router.py       # GET /api/pdf/*
│   ├── png_viewer_router.py # GET /api/png/*
│   ├── admin_router.py     # GET /admin, /api/admin/status (Magic Link)
│   ├── auth_router.py      # Magic Link аутентификация (/api/auth/*)
│   ├── upload_router.py    # GET/POST /api/upload/* (тонкий слой, логика → upload_service)
│   ├── health_router.py    # GET /health
│   ├── sitemap_router.py   # GET /sitemap.xml (динамическая генерация с кешем по reference_version)
│   └── static_router.py    # GET / (SEO-рендер), /robots.txt, /index.html (301→/), /about.html, редиректы, /api/redirect-table
│
├── services/               # Бизнес-логика
│   ├── file_service.py     # Работа с файлами
│   ├── archive_service.py  # ZIP архивы и GPS-треки
│   ├── json_io.py          # BOM-устойчивое чтение JSON (read_json, utf-8-sig)
│   ├── http_cache_utils.py # HTTP кеш (ETag, Last-Modified, 304)
│   ├── id_utils.py         # Нормализация ID отчётов (Шифр→5 цифр, ДопШифр→UPPERCASE)
│   ├── security/           # Валидация путей
│   ├── auth/               # Magic Link, сессии, email; session_helpers — общие auth-хелперы
│   ├── upload/             # Бизнес-логика загрузки: нормализация, уникальность, staging, операции,
│   │                       #   read-модели (read_staged_item, read_published_item)
│   ├── admin/              # Операционная аналитика: status_service (collect_health, collect_status и др.)
│   ├── alerts/             # Email-уведомления (alerter, digest, recipients)
│   ├── database/           # Работа с SQLite; connection (open_tlib_db), search_executor (count_search, execute_search)
│   ├── file_watcher/       # Обработка загрузок (+ notify: письма по факту публикации)
│   ├── cache/              # Управление кешем (LRU, подготовка, pipeline)
│   ├── conversion/         # Конвертация медиа (изображения, PDF→PNG)
│   ├── seo/                # SEO: parse_report_query, fetch_report_row, build_canonical_query,
│   │                       #       build_title/description, render_homepage_html/render_report_html
│   └── validation/         # Валидация данных (кодировка, JSON Schema)
│
├── js/                     # Frontend (Vanilla JS)
│   ├── main.js             # Точка входа
│   ├── admin.js            # Панель администратора
│   ├── upload.js           # Страница загрузки/модерации отчётов (upload.html)
│   ├── config/             # Конфигурация
│   ├── core/               # Состояние приложения
│   ├── modules/            # Модули (search, ui, redirect, sidebarAuth, fileUtils)
│   └── services/           # Сетевые сервисы: authService (/api/auth/*), serverConfigService, referenceListsService, archiveService, cacheWarmService
│
├── data/                   # Архивы отчетов
├── data.db/                # БД SQLite (tlib.db, tlib.xlsx)
├── data.cache/             # Кеш файлов (GPS архивы, извлеченные файлы, PNG)
├── data.up/                # Загрузка файлов (pipeline)
├── data.old/               # Бэкапы файлов и БД
├── data.secret/            # Приватные БД (auth.db, stats.db) — вне STATIC_DIRS
└── logs/                   # Логи приложения
```

---

## Потоки данных

### Поиск в базе данных

```
Browser                 FastAPI                 SQLite
   │                       │                       │
   │  POST /api/search     │                       │
   │  {Год: 2024, ...}     │                       │
   │──────────────────────>│                       │
   │                       │  SELECT * FROM tlib   │
   │                       │  WHERE Год = 2024     │
   │                       │──────────────────────>│
   │                       │                       │
   │                       │    [результаты]       │
   │                       │<──────────────────────│
   │                       │                       │
   │    JSON [{...}, ...]  │                       │
   │<──────────────────────│                       │
```

### Обработка загружаемых файлов

```
data.up/20_go/    30_processing/      data/           (корень)
   │                   │                │                │
   │  новые файлы      │                │                │
   │──────────────────>│                │                │
   │                   │                │                │
   │                   │  валидация     │                │
   │                   │  JSON схема    │                │
   │                   │                │                │
   │                   │  копирование   │                │
   │                   │───────────────>│                │
   │                   │                │                │
   │                   │                │  генерация БД  │
   │                   │                │───────────────>│
   │                   │                │  tlib-new.db   │
   │                   │                │                │
   │ data.new/ или     │                │                │
   │ 40_error/         │                │                │
   │<──────────────────│                │                │
```

### Загрузка отчёта через сайт (/upload.html)

```
Пользователь ──GET /api/upload/status──> {uploads_enabled} (проверка диска при открытии формы)
Пользователь ──POST /api/upload/submit──> data.up/10_up/ (json + файл) ──письмо──> Админы
                                                  │
                          Приём файла (upload_router → upload_io.stream_upload_to_temp):
                              - диск-гард (507 при < 4 ГБ свободно или ≥ 90% занято)
                              - чанковый стрим в _tmp_upload_* (max RAM ≈ 1 МБ)
                              - 413 при превышении MAX_ARCHIVE_SIZE в процессе стрима
                              - атомарный rename temp → <ID>.{zip|pdf}
                                                  │
                              Админ на /upload.html (модерация):
                                  ├─ Опубликовать → data.up/20_go/ ──File Watcher──> data/ + tlib.db
                                  │                                    └─ notify-маркер → письмо автору
                                  └─ Отклонить    → data.old/ + .err ──письмо──> автору
```

### Правка опубликованного отчёта (/upload.html)

```
Автор/Админ ──POST /api/upload/submit-edit──> data.up/10_up/ (json + файл + .editmeta) ──письмо──> Админы
                                                  │
                              Админ модерирует заявку «ред.» (как обычную):
                                  ├─ Опубликовать → data.up/20_go/ ──File Watcher──> data/ + tlib.db
                                  └─ Отклонить    → data.old/ + .err ──письмо──> автору
```

### Удаление опубликованного отчёта (/upload.html)

```
Автор/Админ ──POST /api/upload/request-delete──> data.up/10_up/<ID>.delreq ──письмо──> Админы
                                                  │
                              Админ на /upload.html (запрос «удаление»):
                                  ├─ Подтвердить → data.up/20_go/<ID>.delete ──File Watcher──> бэкап в data.old/ + пересборка БД
                                  │                                                              └─ письмо инициатору
                                  └─ Отклонить   → .delreq удаляется, отчёт остаётся ──письмо──> инициатору
```

Право на правку/удаление имеют администраторы и автор отчёта (по `ЗагрузилID`).

Детали API и аутентификации: [API](details/API.md#загрузка-отчётов-apiupload), [Безопасность](details/SECURITY.md).

---

## Технологический стек

| Компонент | Технология | Версия | Назначение |
|-----------|------------|--------|------------|
| Server | uvicorn | 0.34.3 | ASGI сервер |
| Framework | FastAPI | 0.115.12 | Веб-фреймворк |
| ASGI | Starlette | 0.39.0+ | HTTP, статика, Range requests |
| Database | SQLite | встроен | Хранение данных |
| Validation | jsonschema | 4.20.0+ | Валидация JSON |
| Media | PyMuPDF | 1.23.0+ | Конвертация PDF→PNG, оптимизация изображений |
| Export | openpyxl | 3.1.0+ | Экспорт БД в XLSX |
| Frontend | Vanilla JS | ES6+ | UI без зависимостей |

---

## Фоновые задачи

| Задача | Интервал | Назначение |
|--------|----------|------------|
| file_watcher_task | 60 сек | Обработка файлов в data.up/ (+ stability-window 3 скана) |
| database_watcher_task | 5 сек | Обнаружение tlib-new.db |
| _stats_flush_loop | 5 мин | Сброс in-memory статистики TrafficStats в stats.db |
| _auth_cleanup_loop | 1 час | Очистка просроченных сессий и magic links из auth.db |

---

## Детальная документация

- [Безопасность](details/SECURITY.md) — защиты, заголовки, тестирование
- [Логирование](details/LOGGING.md) — файлы логов, формат, анализ
- [File Watcher](details/FILE_WATCHER.md) — pipeline обработки файлов
- [База данных](details/DATABASE.md) — структура, обновление, кэширование
- [API](details/API.md) — endpoints, параметры, примеры
