# PDF to PNG Auto-Conversion

## Описание

Автоматическая конвертация PDF файлов в директории с PNG страницами при попадании PDF в `data.cache/`.

Работает в фоновом режиме (background task) и не блокирует основные запросы.

Конвертация выполняется **последовательно**: PDF открывается один раз, страницы рендерятся одна за другой и немедленно сохраняются на диск. Пиковое потребление RAM — одна страница, а не весь документ.

Конвертация идёт **по окну просмотра** (см. [«Конвертация, пока смотрят»](#2-конвертация-пока-смотрят)): без зрителя рендерятся только первые 5 страниц, при просмотре — не дальше 20 страниц вперёд от текущей; остальное докручивается по мере листания. Каждая страница рендерится один раз — работа на документ ограничена прочитанной частью, сколько бы раз его ни открывали.

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

**Гейт по жесту пользователя.** Headless-боты (облачные IP) исполняют наш JS, но не двигают мышь и не касаются экрана. Поэтому фронтенд не отправляет запросы, запускающие конвертацию, до первого жеста (`pointerdown`, `pointermove`, `wheel`, `touchstart`, `keydown`; `scroll` и программный `click()` не считаются) — промис `whenUserGesture()` из `js/utils/userGesture.js`:

- карточка отчёта рендерится через `/prepare?probe=1` (без запуска); при `not_prepared` ставится `cacheWarmService.prepareCache`, который стартует на первом жесте — у человека раньше клика по вкладке;
- `cacheWarmService.prepareCache` и `resolveFile` ждут жеста;
- `resolvePdfViewer` ждёт жеста, если отчёта нет в кэше (нет `data-pages-total`), — чтобы лимит ретраев `/pages` у png-viewer отсчитывался от старта конвертации. Кэш с PDF, в том числе на паузе (`partial`), показывается сразу: готовые страницы видны, остальные докручивает опрос `/pages`.

Сервер жест не видит: `/prepare` без `probe` и `/resolve` по-прежнему запускают подготовку. Защита — от ботов, исполняющих наш фронтенд, а не от прямых запросов к API.

Кэшированные отчёты гейтом не закрыты: бот без жеста на кэшированном PDF через heartbeat вьюера получает докрутку окна — не больше `N − K` (15) страниц за визит, дальше окно без листания не сдвигается.

Конвертация запускается при попадании PDF в кеш:

#### Standalone PDF
```
POST /api/cache/{name}/prepare
  ↓
convert_standalone_pdf() → data.cache/{name}/
  ↓
convert_pdf_to_directory() → data.cache/{name}/{name}-png/
```

#### PDF из архива (ZIP)
```
POST /api/cache/{name}/prepare
  ↓
prepare_archive_cache() → extract_files() → convert_pdfs()
  ↓
convert_pdf_to_directory() → data.cache/{name}/{pdf_stem}-png/
```

#### Докрутка PDF на паузе
```
GET /api/png/{name}/{pdf_stem}-png/pages?want=N   (директория частичная, подготовка не идёт)
  ↓
resume_pdf_conversion() — впереди от want готово меньше N/2 страниц? иначе выход без lock
  ↓
convert_pdf_to_directory() (только недостающие страницы окна от want)
```

`/prepare` при валидном кеше PDF на паузе не докручивает.

### 2. Конвертация, пока смотрят

Конвертер рендерит только **окно страниц вокруг той, которую смотрят**. Число визитов (в том числе ботов, прошедших гейт по жесту) работу не умножает: каждая страница рендерится один раз, а без зрителя дело не идёт дальше прогрева.

- **Heartbeat.** Пока на диске не все страницы, png-viewer раз в 2 с опрашивает `GET /api/png/{dir}/pages?want=N`. Роутер пишет в PNG-директорию `_watch.json` — `{"ts": unix-время, "want": N}`, где `want` — страница (1-based), которую сейчас показывает вьюер (`services/cache/cache_watch.py`).
- **Окно рендера** — `first_missing_in_window()` (`cache_watch.py`), общее правило конвертера и докрутки. Перед каждой страницей конвертер читает `_watch.json` и рендерит первую недостающую страницу окна; окно готово — пауза:
  - heartbeat свежее `PDF_CONVERT_IDLE_TIMEOUT_SECONDS` (20 с) и `want` в пределах документа — окно `[want, want + N)`, `N = PDF_CONVERT_LOOKAHEAD_PAGES` (20). Страницы позади `want` рендерятся, только когда `want` туда вернётся;
  - иначе зрителя нет (прогрев по жесту до открытия вкладки, вкладку закрыли) — первые `K = PDF_CONVERT_PREWARM_PAGES` (5) страниц: человек, открыв вкладку, сразу видит начало документа.

  Константы — в `config/cache.py`. `IDLE_TIMEOUT` — только критерий свежести heartbeat, а не бюджет времени рендера. Рендер ~3 стр/с, опрос вьюера раз в 2 с — живой читатель за окно не выходит и заглушек не встречает.
- **Порядок.** Окно пересчитывается перед каждой страницей, поэтому прыжок на дальнюю страницу (deep-link `page=40`, ввод номера) рендерит её первой, дальше — вперёд от неё; следующий опрос вьюера показывает её через ~2 с.
- **Частичное состояние.** Пауза — не ошибка: `_meta.json` пишется как обычно, кеш валиден, LRU видит обычную папку. Запись PDF получает `"status": "partial"` и `pages_done` (`pages` — полное число страниц). Частичная директория не удаляется.
- **Докрутка** — `resume_pdf_conversion()` (`cache_prepare_service.py`): готовые PNG пропускаются (без purge), страница пишется атомарно (tmp + `os.replace`), поэтому обрыв при рестарте не оставляет битый файл. Единственный триггер — `/pages`, если директория частичная (PNG меньше, чем в `_pages_total.txt`) и подготовка архива не идёт. `/prepare` не докручивает: без зрителя окно — первые K страниц, которые уже готовы, и докрутка была бы холостым lock + перераспаковкой.
  - **Гистерезис.** Докрутка выходит сразу — без lock, распаковки и строк в логе, — если впереди от `want` готовы `N/2` (10) страниц подряд. Без него человек, листая, запускал бы докрутку на каждую страницу: lock, `ensure_cache_space` с чтением всех `_meta.json`, перераспаковка PDF из ZIP, две строки лога.
  - **Место в кеше** оценивается по окну, а не по всему PDF: `size × CACHE_PDF_SIZE_MULTIPLIER × min(N, pages) / pages`. Кеш живёт у лимита, и оценка «весь PDF × 3» на каждой докрутке вытесняла бы чужие папки зря.

  PDF из ZIP перераспаковывается в `_work/` одним файлом (`extract_single_member`). По завершении `status` и `pages_done` снимаются, в `app.log` — `PDF conversion resumed` (`rendered`/`done`/`total`/`completed`). При сбое докрутки директория закрывается на готовых страницах: запись получает `"status": "error"`, а `_pages_total.txt` и `pages` в meta — число готовых PNG (без PNG маркер удаляется). Директория перестаёт быть частичной: опрос вьюера не ставит холостую докрутку каждые 2 с, вьюер показывает готовые страницы и не ждёт недостающих; причина — в `app.log`.
- **Наблюдаемость.** Каждый запуск конвертера (подготовка и докрутка) пишет одну INFO-строку `PDF conversion run` (`png_dir`, `rendered` — страниц за запуск, `done`, `total`, `completed`). Нагрузку меряет сумма `rendered` в час: полных конвертаций при рендере по окну почти нет, и их счётчик нагрузку больше не показывает.
- **Фронтенд** (без изменений). `/prepare` отдаёт `pages` — полное число страниц — и для PDF на паузе: с `data-pages-total` вьюер открывается сразу, без жеста, рисует заглушки и показывает готовые страницы. PNG на диске появляются не по порядку, поэтому png-viewer строит список `0..total-1` по имени файла (заглушки на месте недостающих) и при опросе сопоставляет страницы по имени. Открытая вкладка с длинным PDF опрашивает `/pages`, пока на диске не все страницы, — это и есть heartbeat.
- **«В кэш»** в админке — один инкремент на подготовку отчёта: архив распакован, треки и картинки сконвертированы, у каждого PDF готовы первые K страниц, записан `_meta.json` (PDF на паузе тоже считается). Докрутки не считаются — без двойного счёта. С 25.09.2026 до выкладки рендера по окну подготовки с PDF на паузе не учитывались, поэтому провал «В кэш» за эти сутки — артефакт счётчика.

Фоновая вкладка: браузер троттлит таймеры до раза в секунду (сильнее — после 5 минут скрытия), так что двухсекундный опрос переживает переключение вкладки на минуту-другую. Если опрос всё же замер и конвертация встала, следующий `/pages` её возобновит.

Не входит: приоритет между несколькими PDF одного ZIP (смотрят второй, конвертируется первый) — heartbeat у каждой директории свой, порядок файлов прежний. Откат на версию без этой логики требует удалить partial-папки из `data.cache/`: старый код считал бы их готовыми и не докручивал.

### 3. Прогрессивный показ страниц (Variant 2: без resolve round-trip)

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
resolvePdfViewer() — activateViewerIframe после первого жеста пользователя (whenUserGesture)
  ↓
png-viewer.js → GET /api/png/09582/09582-png/pages?want=1 → 404 (директория ещё создаётся)
  ↓
retry каждые 2 сек (до 15 попыток) → "Подготовка страниц..."
  ↓ (pre-scan создал директорию)
GET /pages?want=1 → [] (пусто) + pages_total
  ↓
заглушки 0..pages_total-1, опрос /pages?want=<текущая> каждые 2 сек (heartbeat)
  ↓
новые PNG подставляются по имени файла → страницы отображаются прогрессивно

/prepare (partial path — конвертация на паузе):
  → {files: [{kind:"pdf", pages:251, png_dir:"09582/09582-png", ...}]}  (pages — полное число, status=partial в _meta.json)

buildViewersHtml → data-png-dir="09582/09582-png" data-pages-total="251"
  ↓
resolvePdfViewer() — сразу activateViewerIframe, без ожидания жеста
  ↓
GET /pages?want=1 → [готовые PNG] + pages_total → готовые страницы видны, на остальных заглушки; heartbeat
  (+ resume_pdf_conversion, если впереди от want готово меньше N/2 страниц)
  ↓
опрос /pages?want=<текущая> каждые 2 сек, пока на диске не все страницы
```

Для standalone PDF: `checkFileAvailable` вызывается так же, как для ZIP — `/prepare?probe=1` возвращает `pages`/`png_dir`, когда кеш есть (в том числе PDF на паузе), а конвертацию некэшированного отчёта запускает `prepareCache` после жеста.

**Pre-scan** (`convert_pdfs`) создаёт пустые PNG-директории (`mkdir`) и маркеры `_pages_total.txt` до начала рендеринга — это гарантирует, что `/pages` вернёт `[]` (а не 404) как только pre-scan завершится.

### 4. Структура PNG директории

```
data.cache/09582/
├── 09582-png/
│   ├── 09582_0001.png    (страница 1)
│   ├── 09582_0002.png    (страница 2)
│   ├── ...
│   ├── _pages_total.txt  (число страниц PDF, пишет pre-scan)
│   └── _watch.json       (heartbeat просмотра: {"ts", "want"}, пишет /pages)
├── _prepare.json         (временный, удаляется после завершения)
└── _meta.json            (PDF на паузе: "status": "partial", "pages_done")
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

# Проверить статус (pages_total и число готовых PNG)
curl http://localhost:8080/api/png/09582/09582-png/pages

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
```

### INFO уровень
```
Standalone PDF converted — pdf=09582, pages=150
Cache prepared successfully — archive=12345-TST, partial=True   (partial: в архиве есть PDF на паузе)
PDF conversion run — png_dir=.../data.cache/09582/09582-png, rendered=5, done=5, total=150, completed=False
PDF conversion resumed — archive=09582, png_dir=09582-png, rendered=20, done=25, total=150, completed=False
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
│       │     pre-scan в convert_standalone_pdf и convert_pdfs (_pages_total.txt)
│       ├── convert_pdf_to_directory()  - главная async функция → (pages_done, pages, rendered, completed)
│       └── _convert_pdf_to_directory_sync() - sync реализация (thread pool)
│             пропускает готовые PNG, перед каждой страницей — first_missing_in_window():
│             окно от want (свежий heartbeat) или первые K страниц; окно готово — пауза
│             on_progress(done, page_count) сразу после fitz.open() — сообщает pages_total
│             INFO «PDF conversion run» (rendered/done/total/completed) на каждый запуск
│
├── cache/
│   ├── cache_prepare_service.py    # Точки входа
│   │   ├── convert_standalone_pdf()    - standalone PDF
│   │   │     count_pdf_pages() + png_dir.mkdir() → pre-scan до рендеринга
│   │   │     write_prepare_status(..., pages_total, converting_path)
│   │   ├── prepare_archive_cache()     - PDF из ZIP → convert_pdfs()
│   │   └── resume_pdf_conversion()     - докрутка PDF на паузе (status=partial), без purge;
│   │         выход без lock, если впереди от want готово ≥ N/2 страниц (гистерезис)
│   ├── cache_watch.py              # Heartbeat просмотра и частичное состояние
│   │   ├── touch_watch() / read_watch() - _watch.json {ts, want}
│   │   ├── first_missing_in_window()   - окно рендера: от want (N) или первые K страниц
│   │   └── read_pages_total() / is_partial() - _pages_total.txt против числа PNG
│   └── cache_service.py            # Управление кешем
│       ├── generate_png_filename()     - имя PNG страницы (конвертер и окно рендера)
│       └── ensure_cache_space()        - LRU очистка
│
│   cache_pipeline.py               # convert_pdfs():
│     Pre-scan: png_dir.mkdir() для всех PDF (директории создаются до начала рендеринга)
│     Затем цикл конвертации; PDF на паузе → status=partial, pages_done
│     extract_single_member(), update_meta_file_entry() — для докрутки
│
routers/cache_router.py             # /prepare и /resolve endpoints
│   _format_file_list(): передаёт kind/pages/png_dir из _meta.json фронтенду
│     (pages — полное число страниц, в том числе для partial)
│   prepare_cache(): при валидном кеше PDF на паузе не докручивает
│   resolve_cache_item(): только image/track/all_tracks; kind="pdf" → 400
│     (PDF-вьюер ходит в /api/png/.../pages напрямую)
│
routers/png_viewer_router.py        # GET /api/png/{dir}/pages?want=N
│   touch_watch() — heartbeat; partial и подготовка не идёт → resume_pdf_conversion
│     (единственный триггер докрутки)
│
js/modules/ui/results/single.js     # handleSingleResult:
│   checkFileAvailable() вызывается для всех типов (ZIP и standalone PDF)
│     /prepare?probe=1 (без запуска); not_prepared → prepareCache (ждёт жеста)
│   prepareFiles из /prepare → pages/png_dir пробрасываются в buildViewersHtml
│
js/utils/userGesture.js             # whenUserGesture(): промис первого жеста пользователя
js/services/cacheWarmService.js     # prepareCache/resolveFile ждут whenUserGesture()
│
js/modules/ui/results/viewers/
├── pdfViewer.js                    # Вьюер без resolve round-trip
│     computePngDir() → детерминированный png_dir из archiveName + pdfName
│     buildViewersHtml(): data-png-dir + data-pages-total вшиваются в HTML
│     resolvePdfViewer(): без data-pages-total ждёт жеста → activateViewerIframe (без fetch)
│     buildPngViewerUrl(..., pagesTotal) → hash: dir=...&page=...&total=251
└── viewerHelpers.js                # resolveAndWait используется для image/track, не PDF
│
js/png-viewer.js                    # Прогрессивный показ
    initFromHash(): парсит total= из хеша → this.options.pagesTotal
    loadPages(dirPath, retryCount): retry на 404 и пустой список (до 15 попыток, 2 сек);
      /pages?want=<initialPage+1>, список 0..total-1 по имени файла (_buildPageList)
    _pollNewPages(): /pages?want=<currentPage+1> каждые 2 сек, пока на диске не все страницы —
      heartbeat для конвертера; новые PNG сопоставляются по имени
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
