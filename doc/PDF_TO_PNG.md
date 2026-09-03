# PDF to PNG Auto-Conversion

## Описание

Автоматическая конвертация PDF файлов в директории с PNG страницами при попадании PDF в `data.cache/`.

Работает в фоновом режиме (background task) и не блокирует основные запросы.

Конвертация выполняется **последовательно**: PDF открывается один раз, страницы рендерятся одна за другой и немедленно сохраняются на диск. Пиковое потребление RAM — одна страница, а не весь документ.

Вьюер открывается **мгновенно** без промежуточных спиннеров: `png_dir` вычисляется детерминированно на стороне фронтенда, `pages_total` берётся из `/prepare` ответа (поле `pages` в списке файлов). Resolve round-trip исключён полностью. Если конвертация ещё идёт, `png-viewer.js` сам ретраит `/pages` каждые 2 сек до появления первых PNG. По мере сохранения PNG файлов на диск страницы автоматически подгружаются через `IntersectionObserver`. Если страница ещё не готова — показывается «Страница N подготавливается...» и выполняются повторные попытки с экспоненциальным backoff (3 сек → 4.5 сек → ... до 15 сек).

## Установка зависимостей

```bash
pip install pymupdf
```

## Конфигурация

В `config/media.py` определены следующие параметры:

```python
# Включить/отключить конвертацию
PDF_TO_PNG_ENABLED: bool = False  # True для включения

# Разрешение (DPI)
PDF_TO_PNG_DPI: int = 96  # 72, 96, 150, 300

# Цветовое пространство
PDF_TO_PNG_COLORSPACE: str = "rgb"  # "rgb" или "gray"

# Альфа-канал (прозрачность)
PDF_TO_PNG_ALPHA: bool = False  # True для прозрачного фона
```

## Как работает

### 1. Триггеры конвертации

Конвертация запускается автоматически при попадании PDF в кеш:

#### Standalone PDF
```
GET /api/cache/{name}/resolve
  ↓
convert_standalone_pdf() → data.cache/{name}/
  ↓
convert_pdf_to_directory() → data.cache/{name}/{name}-png/
```

#### PDF из архива (ZIP)
```
GET /api/cache/{name}/prepare
  ↓
prepare_archive_cache() → extract_files() → convert_pdfs()
  ↓
convert_pdf_to_directory() → data.cache/{name}/{pdf_stem}-png/
```

### 2. Прогрессивный показ страниц (Variant 2: без resolve round-trip)

`pdfViewer.js` вычисляет `png_dir` **детерминированно** на клиенте (`computePngDir`) и берёт `pages_total` из `data-pages-total` атрибута контейнера (заполняется при рендере из `/prepare` ответа). Вьюер открывается немедленно без сетевого запроса.

```
/prepare (ready path):
  → {files: [{kind:"pdf", pages:251, png_dir:"09582-png", ...}]}
  
buildViewersHtml → data-png-dir="09582/09582-png" data-pages-total="251"
  ↓
resolvePdfViewer() — читает data-атрибуты, сразу activateViewerIframe (без fetch)
  ↓
png-viewer.js → GET /api/png/09582/09582-png/pages → [251 файл]
  ↓
Все страницы отображаются

/prepare (cold path — конвертация ещё идёт):
  → {files: [{kind:"pdf", ...}]}  (без pages/png_dir — TOC не содержит этих данных)
  
buildViewersHtml → computePngDir() → data-png-dir="09582/09582-png" (без data-pages-total)
  ↓
resolvePdfViewer() — activateViewerIframe немедленно
  ↓
png-viewer.js → GET /api/png/09582/09582-png/pages → 404 (директория ещё создаётся)
  ↓
retry каждые 2 сек (до 15 попыток) → "Подготовка страниц..."
  ↓ (pre-scan создал директорию)
GET /pages → [] (пусто, pagesTotal=0)
  ↓
retry → ... → первые PNG появляются на диске
  ↓
GET /pages → [N файлов] → страницы отображаются прогрессивно
```

Для standalone PDF: `checkFileAvailable` вызывается так же, как для ZIP — `/prepare` запускает конвертацию и возвращает `pages`/`png_dir` когда кеш готов.

**Pre-scan** (`convert_pdfs`) создаёт пустые PNG-директории (`mkdir`) до начала рендеринга — это гарантирует, что `/pages` вернёт `[]` (а не 404) как только pre-scan завершится.

### 3. Структура PNG директории

```
data.cache/09582/
├── 09582-png/
│   ├── 09582_0001.png  (страница 1)
│   ├── 09582_0002.png  (страница 2)
│   └── ...
├── _prepare.json       (временный, удаляется после завершения)
└── _meta.json
```

`_prepare.json` во время конвертации содержит дополнительные поля:
- `pages_total` — число страниц текущего конвертируемого PDF (используется для мониторинга).
- `converting_path` — путь к PDF, который рендерится прямо сейчас.

## Тестирование

### Ручное тестирование

#### 1. Включить конвертацию

В `config/media.py`:
```python
PDF_TO_PNG_ENABLED = True
```

#### 2. Запустить сервер

```bash
python app.py
```

#### 3. Тест standalone PDF

```bash
# Запустить подготовку кэша
curl -X POST http://localhost:8080/api/cache/09582/prepare

# Проверить статус
curl http://localhost:8080/api/cache/09582/resolve

# Проверка результата
ls -d data.cache/09582/*-png/
```

### Проверка содержимого директории

```bash
# Список PNG файлов
ls data.cache/09582/*-png/*.png

# Количество страниц
ls data.cache/09582/*-png/*.png | wc -l

# Просмотр первой страницы (Linux/Mac)
xdg-open data.cache/09582/*-png/*_0001.png
```

## Логирование

### DEBUG уровень
```
Page 1/150 rendered in 1.23s
Page 2/150 rendered in 1.18s
...
PDF rendered: 09582.pdf, 150 pages, 52428800 bytes
```

### INFO уровень
```
Standalone PDF converted — pdf=09582, pages=150
```

### WARNING уровень
```
PDF_TO_PNG_ENABLED=True but PyMuPDF is not installed. Install with: pip install pymupdf
```

### ERROR уровень
```
Error converting PDF to directory: [описание ошибки]
```

## Управление кешем

### LRU очистка

PNG директории участвуют в общем LRU кеше `data.cache/`:
- При превышении `MAX_CACHE_SIZE` удаляются самые старые директории
- mtime обновляется при каждом cache hit
- Освобождение места происходит автоматически перед конвертацией

### Ручная очистка

```bash
# Удалить PNG директорию конкретного отчёта
rm -rf data.cache/09582/*-png/

# Очистить весь кеш
rm -rf data.cache/*
```

### Статистика

```bash
# Количество PNG директорий
find data.cache -type d -name "*-png" | wc -l

# Общий размер всех PNG
du -sh data.cache/*/*-png/

# Топ-10 самых больших директорий
du -sh data.cache/*/*-png/ | sort -rh | head -10
```

## Производительность

### Рекомендации по DPI

| DPI | Качество | Размер PNG | Скорость | Рекомендация |
|-----|----------|------------|----------|--------------|
| 72  | Экранное | ~200 KB    | Быстро   | Предпросмотр |
| 96  | Веб      | ~350 KB    | Средне   | **Оптимально** |
| 150 | Хорошее  | ~800 KB    | Медленно | Детальный просмотр |
| 300 | Печать   | ~3 MB      | Очень медленно | Только при необходимости |

### Оценка времени конвертации

Примерное время для PDF на 100 страниц (последовательный рендеринг):

| DPI | Время | Размер директории |
|-----|-------|-------------------|
| 72  | ~40 сек  | ~20 MB |
| 96  | ~65 сек  | ~35 MB |
| 150 | ~150 сек | ~80 MB |
| 300 | ~450 сек | ~300 MB |

## Архитектура

```
services/
├── conversion/
│   └── pdf_to_png_service.py       # Сервис конвертации
│       ├── count_pdf_pages()           - быстрый подсчёт страниц (< 100 мс, без рендеринга)
│       │     используется только в convert_standalone_pdf (мониторинг прогресса)
│       ├── convert_pdf_to_directory()  - главная async функция
│       ├── _convert_pdf_to_directory_sync() - sync реализация (thread pool)
│       │     on_progress(0, page_count) сразу после fitz.open() — сообщает pages_total
│       └── generate_png_filename()  - генерация имён файлов
│
├── cache/
│   ├── cache_prepare_service.py    # Точки входа
│   │   ├── convert_standalone_pdf()    - standalone PDF
│   │   │     count_pdf_pages() + png_dir.mkdir() → pre-scan до рендеринга
│   │   │     write_prepare_status(..., pages_total, converting_path)
│   │   └── prepare_archive_cache()     - PDF из ZIP → convert_pdfs()
│   └── cache_service.py            # Управление кешем
│       └── ensure_cache_space()        - LRU очистка
│
│   cache_pipeline.py               # convert_pdfs():
│     Pre-scan: png_dir.mkdir() для всех PDF (директории создаются до начала рендеринга)
│     _format_file_list(): pages + png_dir из _meta.json попадают в /prepare ответ
│     Затем цикл конвертации
│
routers/cache_router.py             # /prepare и /resolve endpoints
│   _format_file_list(): передаёт kind/pages/png_dir из _meta.json фронтенду
│   resolve_cache_item():
│     - ready + kind="pdf" → {status:"ready", png_dir, pages}  (Step 1)
│     - preparing → {status:"preparing", stage, detail}  (Step 2 — без PDF-специфики)
│
js/modules/ui/results/single.js     # handleSingleResult:
│   checkFileAvailable() вызывается для всех типов (ZIP и standalone PDF)
│   prepareFiles из /prepare → pages/png_dir пробрасываются в buildViewersHtml
│
js/modules/ui/results/viewers/
├── pdfViewer.js                    # Вьюер без resolve round-trip
│     computePngDir() → детерминированный png_dir из archiveName + pdfName
│     buildViewersHtml(): data-png-dir + data-pages-total вшиваются в HTML
│     resolvePdfViewer(): читает data-атрибуты → activateViewerIframe (без fetch)
│     buildPngViewerUrl(..., pagesTotal) → hash: dir=...&page=...&total=251
└── viewerHelpers.js                # resolveAndWait используется для image/track, не PDF
│
js/png-viewer.js                    # Прогрессивный показ
    initFromHash(): парсит total= из хеша → this.options.pagesTotal
    loadPages(dirPath, retryCount): retry на 404 и пустой список (до 15 попыток, 2 сек)
    loadPageImage(): 404 → "Подготавливается..." + retry с backoff
│
config/media.py                     # Параметры
└── PDF_TO_PNG_*                    - 4 параметра конфигурации
```

## Troubleshooting

### PyMuPDF не установлен

**Симптом:**
```
WARNING: PDF_TO_PNG_ENABLED=True but PyMuPDF is not installed
```

**Решение:**
```bash
pip install pymupdf
```

### PNG директории не создаются

**Проверка:**
1. `PDF_TO_PNG_ENABLED = True` в config/media.py
2. PyMuPDF установлен: `python -c "import fitz; print(fitz.__version__)"`
3. Проверить логи: `tail -f logs/app.log`

### Медленная конвертация

**Рекомендации:**
1. Уменьшить DPI: `PDF_TO_PNG_DPI = 72`
2. Использовать grayscale: `PDF_TO_PNG_COLORSPACE = "gray"`

### Большой размер PNG директорий

**Рекомендации:**
1. Уменьшить DPI
2. Использовать grayscale (на 30-40% меньше размера)
3. Очистить старые версии вручную

## Ограничения

- Максимальный размер PDF ограничен `MAX_FILE_SIZE` (2 GB)
- PNG директории участвуют в общем лимите `MAX_CACHE_SIZE` (50 GB)
- Конвертация выполняется последовательно в thread pool — одна страница за раз
- При ошибке конвертации основной запрос не блокируется
