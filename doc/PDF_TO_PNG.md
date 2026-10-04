# PDF to PNG Auto-Conversion

## Описание

Автоматическая конвертация PDF файлов в директории с PNG страницами при попадании PDF в `data.cache/`.

Работает в фоновом режиме (background task) и не блокирует основные запросы.

Конвертация выполняется **последовательно**: PDF открывается один раз, страницы рендерятся одна за другой и немедленно сохраняются на диск. Пиковое потребление RAM — одна страница, а не весь документ.

Конвертация идёт **по окну просмотра** (см. [«Конвертация, пока смотрят»](#2-конвертация-пока-смотрят)): без зрителя рендерятся только первые 5 страниц, при просмотре — сначала текущая и по 3 соседних с обеих сторон вперемешку, затем окно до 20 страниц вперёд от текущей, затем, пока документ открыт, страницы позади неё; остальное докручивается по мере листания. Каждая страница рендерится один раз — работа на документ ограничена страницами до самой дальней просмотренной и окном впереди, сколько бы раз его ни открывали.

Вьюер открывается **мгновенно** без промежуточных спиннеров: `png_dir` вычисляется детерминированно на стороне фронтенда, `pages_total` берётся из `/prepare` ответа (поле `pages` в списке файлов). Resolve round-trip исключён полностью. Если конвертация ещё идёт, `png-viewer.js` сам ретраит `/pages` каждые 2 сек до появления первых PNG. По мере сохранения PNG файлов на диск страницы автоматически подгружаются через `IntersectionObserver`. Если страница ещё не готова — показывается «Страница N подготавливается...», а PNG не запрашивается, пока файл не появится в ответе `/pages` (без 404 по заглушкам). Пока страницы на экране нет, `/pages` работает как long-poll: сервер держит запрос до 1,5 с и отвечает в момент появления PNG, поэтому страница показывается сразу после рендера, а не на следующем опросе. Сбой загрузки готового PNG (429, сеть) повторяется до 3 раз с паузой 3 сек и только для страницы на экране; дальше — «не удалось загрузить», новый цикл попыток — при возврате к странице.

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

# Число процессов-воркеров рендера
PDF_RENDER_WORKERS: int = 1
```

## Как работает

### 1. Триггеры конвертации

**Гейт по жесту пользователя.** Headless-боты (облачные IP) исполняют наш JS, но не двигают мышь и не касаются экрана. Поэтому фронтенд не отправляет запросы, запускающие конвертацию, до первого жеста (`pointerdown`, `pointermove`, `wheel`, `touchstart`, `keydown`; `scroll` и программный `click()` не считаются) — промис `whenUserGesture()` из `js/utils/userGesture.js`:

- карточка отчёта рендерится через `/prepare?probe=1` (без запуска); при `not_prepared` ставится `cacheWarmService.prepareCache`, который стартует на первом жесте — у человека раньше клика по вкладке;
- `cacheWarmService.prepareCache` и `resolveFile` ждут жеста;
- `resolvePdfViewer` ждёт жеста, если отчёта нет в кэше (нет `data-pages-total`), — чтобы лимит ретраев `/pages` у png-viewer отсчитывался от старта конвертации. Кэш с PDF, в том числе на паузе (`partial`), показывается сразу: готовые страницы видны, остальные докручивает опрос `/pages`.

Сервер жест не видит: `/prepare` без `probe` и `/resolve` по-прежнему запускают подготовку. Защита — от ботов, исполняющих наш фронтенд, а не от прямых запросов к API.

Кэшированные отчёты гейтом не закрыты: бот без жеста на кэшированном PDF через heartbeat вьюера получает докрутку окна — с первой страницы не больше `N − K` (15) страниц за визит, дальше окно без листания не сдвигается. С deep-link на страницу p — ещё и страницы позади p, пока вкладка открыта, и после ухода не больше 5 с (`PDF_CONVERT_BACKFILL_FRESH_SECONDS`) — около 15 страниц.

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
resume_pdf_conversion() — впереди от want готово меньше N/2 страниц или позади want есть недостающие? иначе выход без lock
  ↓
convert_pdf_to_directory() (недостающие страницы окна от want, затем позади want)
```

`/prepare` при валидном кеше PDF на паузе не докручивает.

### 2. Конвертация, пока смотрят

Конвертер рендерит только **окно страниц вокруг той, которую смотрят**. Число визитов (в том числе ботов, прошедших гейт по жесту) работу не умножает: каждая страница рендерится один раз, а без зрителя дело не идёт дальше прогрева.

- **Heartbeat.** Пока на диске не все страницы, png-viewer опрашивает `GET /api/png/{dir}/pages?want=N`: раз в 2 с, пока текущая страница готова; пока её нет — через 0,3 с после ответа, а сервер сам держит запрос до появления PNG (long-poll, ниже), то есть около раза в 1,8 с; при переходе на неготовую страницу — сразу, но не чаще раза в 0,5 с (константы `PAGES_POLL_*` в `js/png-viewer.js`). Чаще нельзя: лимит API — 300 запросов в минуту на IP, за NAT его делят несколько читателей. Роутер пишет в PNG-директорию `_watch.json` — `{"ts": unix-время, "want": N}`, где `want` — страница (1-based), которую сейчас показывает вьюер (`services/cache/cache_watch.py`). Прыжок на другую неготовую страницу обрывает висящий запрос (`AbortController`); серверная корутина long-poll досыпает не дольше 1,5 с и уходит в уже закрытое соединение.
- **Long-poll.** Директория частичная, а PNG страницы `want` нет на диске — `/pages` ждёт его, проверяя файл раз в `PNG_PAGES_WAIT_STEP_SECONDS` (0,1 с), и отвечает, как только он появился, но не позже `PNG_PAGES_WAIT_SECONDS` (1,5 с); листинг собирается после ожидания. Читатель видит страницу через рендер плюс не больше 0,1 с, а не на следующем опросе. Готовая директория и готовая страница `want` отвечают без ожидания. Запрос, который держит сервер, — корутина на `asyncio.sleep`, поток и воркер рендера он не занимает. Откат без правки кода: `PNG_PAGES_WAIT_SECONDS = 0` и `PDF_CONVERT_NEAR_PAGES = 0` в `config/cache.py` возвращают прежнее поведение сервера; вьюер при этом опрашивает ждущую страницу раз в 0,5 с (`PAGES_POLL_MIN_GAP_MS`) — вдвое чаще прежнего, поэтому при откате под нагрузкой за NAT вернуть и `PAGES_POLL_WAITING_MS = 1000`.
- **Окно рендера** — `first_missing_in_window()` (`cache_watch.py`), общее правило конвертера и докрутки. Перед каждой страницей конвертер читает `_watch.json` и рендерит следующую недостающую страницу; рендерить нечего — пауза:
  - heartbeat свежее `PDF_CONVERT_IDLE_TIMEOUT_SECONDS` (20 с) и `want` в пределах документа — окно `[want, want + N)`, `N = PDF_CONVERT_LOOKAHEAD_PAGES` (20), и, пока heartbeat не старше `PDF_CONVERT_BACKFILL_FRESH_SECONDS` (5 с), недостающие страницы позади `want`. Иначе читатель, прыгнувший вперёд и вернувшийся, снова ждал бы рендера пропущенных страниц. Порядок при heartbeat ≤ 5 с: `want`, затем соседи вперемешку — `want+1`, `want-1`, `want+2`, `want-2`, … на `PDF_CONVERT_NEAR_PAGES` (3) пары, затем остаток окна вперёд, затем позади от ближайшей к началу: читатель, открывший deep-link на середину, часто листает на шаг назад и не должен ждать всё окно вперёд. Heartbeat старше 5 с — только окно вперёд, от `want`: читатель на готовой странице опрашивает раз в 2 с и под порог попадает всегда, а вкладка, которую закрыли (или бот, ушедший с deep-link), назад не рисует;
  - иначе зрителя нет (прогрев по жесту до открытия вкладки, вкладку закрыли) — первые `K = PDF_CONVERT_PREWARM_PAGES` (5) страниц: человек, открыв вкладку, сразу видит начало документа.

  Константы — в `config/cache.py`. `IDLE_TIMEOUT` — только критерий свежести heartbeat, а не бюджет времени рендера. Рендер ~3 стр/с, опрос вьюера раз в 1–2 с — живой читатель за окно не выходит и заглушек не встречает.
- **Порядок.** Окно пересчитывается перед каждой страницей, поэтому прыжок на дальнюю страницу (deep-link `page=40`, ввод номера) рендерит её первой, дальше — соседей с обеих сторон, вперёд от неё, затем назад. Вьюер сообщает новый `want` сразу при переходе, а пока страницы нет, сервер держит запрос до её появления: ожидание — рендер страницы плюс не больше 0,1 с. Переход, пока запрос со старым `want` ещё висит, обрывает его и шлёт новый `want` сразу (не чаще раза в 0,5 с).
- **Частичное состояние.** Пауза — не ошибка: `_meta.json` пишется как обычно, кеш валиден, LRU видит обычную папку. Запись PDF получает `"status": "partial"` и `pages_done` (`pages` — полное число страниц). Частичная директория не удаляется.
- **Докрутка** — `resume_pdf_conversion()` (`cache_prepare_service.py`): готовые PNG пропускаются (без purge), страница пишется атомарно (tmp + `os.replace`), поэтому обрыв при рестарте не оставляет битый файл. Единственный триггер — `/pages`, если директория частичная (PNG меньше, чем в `_pages_total.txt`) и подготовка архива не идёт. Докрутка стартует до ответа и до long-poll (`asyncio.create_task` + `sleep(0)`): за этот шаг она решает, докручивать ли, и пишет `_prepare.json`, поэтому long-poll ждёт страницу, которую уже рисуют. `/prepare` не докручивает: без зрителя окно — первые K страниц, которые уже готовы, и докрутка была бы холостым lock + перераспаковкой.
  - **Гистерезис.** Докрутка выходит сразу — без lock, распаковки и строк в логе, — если впереди от `want` готовы `N/2` (10) страниц подряд и позади `want` недостающих нет. Без него человек, листая, запускал бы докрутку на каждую страницу: lock, `ensure_cache_space` с чтением всех `_meta.json`, перераспаковка PDF из ZIP, две строки лога. Дырки позади `want` заполняет один запуск целиком — за один lock и одну распаковку, пока heartbeat свежий.
  - **Место в кеше** освобождается после записи `_meta.json`, по фактическому размеру папки: `ensure_cache_space(cache_size_bytes)` держит «своя + чужие ≤ `MAX_CACHE_SIZE`». Заранее ничего не оценивается — оценка ошибалась в обе стороны и на каждой докрутке вытесняла бы чужие папки зря. `cache_size_bytes` не включает `_work/` с перераспакованным PDF, lock и `_prepare.json` — они удаляются при снятии lock.

  PDF из ZIP перераспаковывается в `_work/` одним файлом (`extract_single_member`). По завершении `status` и `pages_done` снимаются, в `app.log` — `PDF conversion resumed` (`rendered`/`done`/`total`/`completed`/`extract_ms` — перераспаковка из ZIP, 0 для standalone PDF). При сбое докрутки директория закрывается на готовых страницах: запись получает `"status": "error"`, а `_pages_total.txt` и `pages` в meta — число готовых PNG (без PNG маркер удаляется). Директория перестаёт быть частичной: опрос вьюера не ставит холостую докрутку на каждый запрос, вьюер показывает готовые страницы и не ждёт недостающих; причина — в `app.log`.
- **Наблюдаемость.** Каждый запуск конвертера (подготовка и докрутка) пишет одну INFO-строку `PDF conversion run` (`png_dir`, `rendered` — страниц за запуск, `done`, `total`, `completed`, `render_ms` — длительность запуска, включая ожидание свободного потока и воркера: страницы PDF, которые рендерятся одновременно, чередуются в одном процессе-воркере). Нагрузку меряет сумма `rendered` в час: полных конвертаций при рендере по окну почти нет, и их счётчик нагрузку больше не показывает. Ожидание читателя — INFO `PDF page wait` из `/pages` (`archive`, `png_dir`, `want`, `done` — PNG на диске после ожидания, `total`, `converting` — `converting_path` из `_prepare.json` к началу ожидания, пусто, если конвертация архива не идёт, `waited_ms` — сколько запрос ждал, `ready` — появилась ли страница за ожидание): одна строка на запрос, в начале которого PNG страницы `want` частичной директории не было на диске, то есть пока на экране «Страница N подготавливается…». Разбор причин — [«Читатель видит заглушки»](#читатель-видит-заглушки-что-смотреть-в-логах).
- **Фронтенд.** Вьюер стартует только для видимого PDF (`autoLoad` в `pdfViewer.js`: PDF из `file` в hash, иначе активный или первый — deep-link на второй PDF запускает один вьюер), вьюеры остальных PDF отчёта — при первом выборе их ссылки (`selectLink`): каждый запущенный png-viewer держит heartbeat своей директории, и скрытые PDF одного ZIP не отнимают конвертер у видимого. `/prepare` отдаёт `pages` — полное число страниц — и для PDF на паузе: с `data-pages-total` вьюер открывается сразу, без жеста, рисует заглушки и показывает готовые страницы. PNG на диске появляются не по порядку, поэтому png-viewer строит список `0..total-1` по имени файла (заглушки на месте недостающих) и при опросе сопоставляет страницы по имени. Открытая вкладка с длинным PDF опрашивает `/pages`, пока на диске не все страницы, — это и есть heartbeat.
- **«В кэш»** в админке — один инкремент на подготовку отчёта: архив распакован, треки и картинки сконвертированы, у каждого PDF готовы первые K страниц, записан `_meta.json` (PDF на паузе тоже считается). Докрутки не считаются — без двойного счёта. С 25.09.2026 до выкладки рендера по окну подготовки с PDF на паузе не учитывались, поэтому провал «В кэш» за эти сутки — артефакт счётчика.

Фоновая вкладка: браузер троттлит таймеры до раза в секунду (сильнее — после 5 минут скрытия), так что опрос раз в 1–2 с переживает переключение вкладки на минуту-другую. Если опрос всё же замер и конвертация встала, следующий `/pages` её возобновит.

Не входит: приоритет между несколькими PDF одного ZIP. Конкурируют только PDF, которые уже открывали в этом визите: вьюер, запущенный выбором ссылки, продолжает опрашивать `/pages` и после переключения на другой PDF, а вьюер, который не открывали, не запущен и heartbeat не даёт. Heartbeat у каждой директории свой, порядок файлов прежний. Откат на версию без этой логики требует удалить partial-папки из `data.cache/`: старый код считал бы их готовыми и не докручивал.

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
заглушки 0..pages_total-1, опрос /pages?want=<текущая> каждые 1–2 сек (heartbeat; пока want нет — long-poll до 1,5 сек)
  ↓
новые PNG подставляются по имени файла → страницы отображаются прогрессивно

/prepare (partial path — конвертация на паузе):
  → {files: [{kind:"pdf", pages:251, png_dir:"09582/09582-png", ...}]}  (pages — полное число, status=partial в _meta.json)

buildViewersHtml → data-png-dir="09582/09582-png" data-pages-total="251"
  ↓
resolvePdfViewer() — сразу activateViewerIframe, без ожидания жеста
  ↓
GET /pages?want=1 → [готовые PNG] + pages_total → готовые страницы видны, на остальных заглушки; heartbeat
  (+ resume_pdf_conversion до ответа, если впереди от want готово меньше N/2 страниц или позади want есть недостающие;
   PNG want нет — ответ, как только он появится, но не позже 1,5 сек)
  ↓
опрос /pages?want=<текущая> каждые 1–2 сек, пока на диске не все страницы
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
PDF conversion run — png_dir=.../data.cache/09582/09582-png, rendered=5, done=5, total=150, completed=False, render_ms=1840
PDF conversion resumed — archive=09582, png_dir=09582-png, rendered=20, done=25, total=150, completed=False, extract_ms=0
PDF page wait — archive=09582, png_dir=09582-png, want=40, done=26, total=150, converting=09582.pdf, waited_ms=420, ready=True
```

### WARNING уровень
```
PDF_TO_PNG_ENABLED=True but PyMuPDF is not installed. Install with: pip install pymupdf
Orphaned cache lock removed — archive=09582, age_seconds=412   (lockdir без _prepare.json после рестарта)
PDF render worker died (BrokenProcessPool), pool reset — pdf=.../09582.pdf, page=37   (page пуст, если упал page_count)
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
- Освобождение места происходит автоматически после записи `_meta.json`, по фактическому размеру папки

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
│   ├── pdf_to_png_service.py       # Сервис конвертации
│   │   ├── count_pdf_pages()           - быстрый подсчёт страниц (< 100 мс, без рендеринга)
│   │   │     pre-scan в convert_standalone_pdf и convert_pdfs (_pages_total.txt)
│   │   ├── convert_pdf_to_directory()  - главная async функция → (pages_done, pages, rendered, completed)
│   │   ├── _convert_pdf_to_directory_sync() - sync реализация (thread pool)
│   │   │     пропускает готовые PNG, перед каждой страницей — first_missing_in_window():
│   │   │     want и соседи вперемешку, окно вперёд, затем позади want (свежий heartbeat),
│   │   │     или первые K страниц;
│   │   │     рендерить нечего — пауза; страница — pool.submit(render_page).result()
│   │   │     on_progress(done, page_count) сразу после page_count() в воркере — сообщает pages_total
│   │   │     INFO «PDF conversion run» (rendered/done/total/completed/render_ms) на каждый запуск
│   │   │     BrokenProcessPool → _reset_pool(), WARNING, запуск завершается ошибкой (без retry)
│   │   └── _get_pool() / shutdown_render_pool() - ProcessPoolExecutor (spawn, PDF_RENDER_WORKERS),
│   │         создаётся первым рендером, останавливается в lifespan shutdown с wait=True —
│   │         uvicorn после lifespan переподнимает SIGTERM и умирает без atexit
│   └── pdf_render_worker.py        # Процесс-воркер: только stdlib + fitz (без config/logging_config)
│       ├── page_count()                - число страниц
│       ├── render_page()               - рендер страницы → PNG tmp + os.replace
│       └── ignore_stop_signals()       - SIGTERM/SIGINT получает только родитель
│
├── cache/
│   ├── cache_prepare_service.py    # Точки входа
│   │   ├── convert_standalone_pdf()    - standalone PDF
│   │   │     count_pdf_pages() + png_dir.mkdir() → pre-scan до рендеринга
│   │   │     write_prepare_status(..., pages_total, converting_path)
│   │   ├── prepare_archive_cache()     - PDF из ZIP → convert_pdfs()
│   │   └── resume_pdf_conversion()     - докрутка PDF на паузе (status=partial), без purge;
│   │         выход без lock, если впереди от want готово ≥ N/2 страниц и позади дырок нет
│   │         (гистерезис); extract_ms в «PDF conversion resumed»
│   ├── cache_watch.py              # Heartbeat просмотра и частичное состояние
│   │   ├── touch_watch() / read_watch() - _watch.json {ts, want}
│   │   ├── first_missing_in_window()   - окно рендера: want, соседи ±NEAR вперемешку, остаток окна (N),
│   │   │                                  затем позади want; или первые K
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
│     (единственный триггер докрутки; create_task до ответа)
│   PNG want нет на диске частичной директории → long-poll до PNG_PAGES_WAIT_SECONDS,
│     затем листинг; INFO «PDF page wait» — одна на запрос (converting из _prepare.json, waited_ms, ready)
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
│     autoLoad(): resolvePdfViewer только для видимого PDF — из file в hash, иначе активного/первого;
│       selectLink(): resolvePdfViewer для ещё не запущенного вьюера выбранного PDF
│     buildPngViewerUrl(..., pagesTotal) → hash: dir=...&page=...&total=251
└── viewerHelpers.js                # resolveAndWait используется для image/track, не PDF
│
js/png-viewer.js                    # Прогрессивный показ
    initFromHash(): парсит total= из хеша → this.options.pagesTotal
    loadPages(dirPath, retryCount): retry на 404 и пустой список (до 15 попыток, 2 сек);
      /pages?want=<initialPage+1>, список 0..total-1 по имени файла (_buildPageList)
    _pollNewPages(): /pages?want=<currentPage+1>, пока на диске не все страницы, —
      heartbeat для конвертера; новые PNG сопоставляются по имени
    _schedulePoll(): единственный таймер опроса, один запрос в полёте; через 0,3 с после ответа, пока
      текущей страницы нет на диске (сервер держит запрос до 1,5 с), иначе раз в 2 с; не чаще раза в 0,5 с
    _onCurrentPageChanged(): скролл или переход на неготовую страницу → abort висящего /pages и опрос сразу с новым want
    loadPageImage(): PNG не в /pages → "Подготавливается..." без запроса (загрузит _pollNewPages);
      сбой готового PNG (429/сеть) → до IMAGE_RETRY_MAX (3) повторов через 3 сек, только на экране
│
config/media.py                     # Параметры
├── PDF_TO_PNG_*                    - 4 параметра конфигурации
└── PDF_RENDER_WORKERS              - число процессов-воркеров рендера (1)
```

## Troubleshooting

### Читатель видит заглушки: что смотреть в логах

«Страница N подготавливается…» на экране — это `PDF page wait` в `app.log`: одна строка на запрос `/pages`, в начале которого страницы `want` не было на диске. Запрос ждёт её до 1,5 с (long-poll), поэтому, пока страница не готова, строк — около одной в 1,8 с; `ready=True` — страница появилась за ожидание и ушла в ответе, `waited_ms` — сколько читатель ждал в этом запросе. Лимит запросов — `RATE_LIMIT_EXCEEDED` в `critical.log` с `ip` и `endpoint`. `Lock busy … skip PDF resume` сигналом конкуренции не служит: пока один PDF архива конвертируется, докрутка другого до lock не доходит (её не ставят, пока `_prepare.json` занят), к тому же строка пишется только в `debug.log`.

Сигнал → вывод → правка:

- **Норма.** После открытия частичного PDF или прыжка на дальнюю страницу — одна `PDF page wait` с `ready=True` и `waited_ms` порядка рендера страницы (сотни мс; первая докрутка PDF из ZIP — плюс перераспаковка, `extract_ms`). `converting` — свой PDF: докрутку ставит этот же запрос до ожидания. Следом `PDF conversion resumed` (при подготовке — `PDF conversion run`) с `rendered > 0`. Правка не нужна. По суткам `PDF page wait`: большинство строк — `ready=True`, ожидание закрывается внутри одного запроса. Heartbeat — около 30 запросов `/pages` в минуту на каждый запущенный вьюер частичного PDF (видимый и открытые ранее в этом визите), до ~33, пока текущей страницы вьюера нет на диске (запрос держится до 1,5 с плюс 0,3 с паузы); готовый PDF — один запрос при открытии.
- **`PDF page wait` с `ready=False`, `waited_ms` ≈ 1500, `converting` — свой PDF, за ней `ready=True`** → страница `want` рендерилась дольше 1,5 с: перераспаковка PDF из ZIP перед первой страницей докрутки или тяжёлая страница. Ответ ушёл по таймауту, следующий запрос через 0,3 с подхватил страницу. Правка не нужна; если таких пар много и `extract_ms` велик — держать распакованный PDF, пока документ недорисован.
- **`RATE_LIMIT_EXCEEDED endpoint=/api/png/…/pages`, у одного `ip` разные `png_dir`** → за одним адресом несколько читателей (NAT). При 300 запросах в минуту это до 10 запущенных вьюеров частичных PDF (до 9, если все ждут страницу), если другого API с адреса нет; 429 — с 301-го запроса за минуту. Пока окно лимита не сбросится, heartbeat не доходит до сервера, и через 20 с конвертер встаёт на паузу. → Отдельная корзина для heartbeat `/pages` в `middlewares/rate_limit.py` или поднять `RATE_LIMIT_REQUESTS_PER_MINUTE`.
- **`RATE_LIMIT_EXCEEDED endpoint=/cache/…`** → с IP больше 5000 запросов статики в минуту: скрейпинг PNG или слишком широкий префетч вьюера (счётчик статики общий с `/data/`, `/js/`, `/css/`, см. [SECURITY.md](details/SECURITY.md#2-rate-limiting)). → Отдельная корзина `/cache/` с порогом ниже 5000 или уменьшить `PRELOAD_MARGIN` в `js/png-viewer.js`.
- **`PDF page wait`, `converting` — другой PDF того же архива** → конкуренция PDF одного ZIP: докрутку этого PDF не ставят, пока другой PDF архива не отрендерит своё окно (или пока идёт подготовка архива). Бывает, когда в визите открывали несколько PDF отчёта, и при холодной подготовке архива с несколькими PDF. → Lock и `_prepare.json` на `png_dir` вместо архива.
- **`PDF page wait`, `converting` — свой PDF, `done` растёт, но `want` уходит вперёд быстрее** → рендер медленнее чтения. → Увеличить `PDF_CONVERT_LOOKAHEAD_PAGES` или снизить `PDF_TO_PNG_DPI`.
- **`PDF page wait`, `converting` — свой PDF, `done` не растёт** → подготовка или докрутка оборвалась (рестарт `tlibapp`, отгрузка), `_prepare.json` и lock остались. → Правка не нужна: через `CACHE_STALE_LOCK_TIMEOUT_MINUTES` (5 мин) `_prepare.json` считается устаревшим, и следующая докрутка его очищает.
- **`PDF page wait` с пустым `converting` и `ready=False` несколько запросов подряд, без 429** → докрутка ставится, но выходит, не начав. Гистерезис эту строку не вызывает: окно проверки начинается с `want`, и отсутствующая страница `want` всегда запускает докрутку. Смотреть в папке архива: `_prepare.json` без `converting_path` — идёт распаковка при подготовке, ждать; `_prepare.lockdir` без `_prepare.json` — осиротевший lock после рестарта: снимается сам при следующей докрутке, когда lockdir старше `CACHE_STALE_LOCK_TIMEOUT_MINUTES` (5 мин), в `critical.log` — `Orphaned cache lock removed` (WARNING в `app.log` не попадает); до этого — ждать; запись PDF в `_meta.json` не `partial` или источника нет в `data/` — рендерить не из чего.
- **Много `PDF conversion resumed` с `rendered` 1–3** → докрутка дорогая на страницу (lock, `ensure_cache_space`, перераспаковка из ZIP). При чтении подряд `rendered` — около N/2: докрутка стартует, когда впереди готово меньше N/2 страниц, и дорисовывает окно до N. Дыры позади `want` после прыжка дозаполняются тем же запуском, пока документ открыт. Мелкие запуски — конец документа и дырки, оставшиеся после ухода зрителя, это норма. Цена запуска — `extract_ms` этой строки против `render_ms` строки `PDF conversion run` того же запуска: если перераспаковка сравнима с рендером, держать распакованный PDF, пока документ недорисован. Если мелких запусков много при обычном листании → докручивать реже: уменьшить порог гистерезиса `PDF_CONVERT_LOOKAHEAD_PAGES // 2` в `resume_pdf_conversion` (докрутка стартует позже и рендерит больше за запуск) или увеличить `PDF_CONVERT_LOOKAHEAD_PAGES`.

### Процесс-воркер рендера

Рендер страниц идёт в отдельном процессе: PyMuPDF держит GIL весь рендер страницы, и в потоке главного процесса запросы (поиск, `/pages`, PNG) ждали бы конца окна рендера.

- **Дочерний процесс `python … spawn_main` в `ps`** → это воркер рендера; появляется с первым рендером после рестарта, вместе с ним — `python … resource_tracker` (служебный процесс multiprocessing, несколько МБ). После отгрузки посмотреть память воркера: `ps -o pid,rss,cmd --ppid <pid uvicorn>`. Норма — десятки МБ плюс store-кэш MuPDF (до 256 МБ). Если RSS растёт от рендера к рендеру → пересоздавать воркер через N задач: `max_tasks_per_child` у `ProcessPoolExecutor` в `_get_pool()` (Python ≥ 3.11).
- **`PDF render worker died (BrokenProcessPool), pool reset` в `critical.log`** → воркер умер посреди запуска (segfault MuPDF на странице `page`, OOM killer). Запуск завершился ошибкой (`Error converting PDF to directory` рядом), сервер жив, следующий рендер создаёт воркер заново. Повторяется на том же `pdf` и `page` → PDF роняет MuPDF: проверить файл отдельно (`python -c "import fitz; fitz.open('<pdf>')[<page-1>].get_pixmap()"`). Разные PDF → искать OOM killer в `journalctl -k`.
- **Рестарт `tlibapp` посреди рендера** → не ошибка: systemd шлёт SIGTERM всем процессам сервиса, воркер его игнорирует (`ignore_stop_signals`) и дорисовывает окно, пока uvicorn ждёт фоновые задачи. Не уложились в `TimeoutStopSec` — SIGKILL всем процессам, запуск обрывается, как и до воркера (см. «`done` не растёт» выше). `BrokenProcessPool` рядом с рестартом — воркер убили отдельно от uvicorn (OOM killer, ручной `kill`). `State 'stop-sigterm' timed out` и `Killing process` в `journalctl -u tlibapp` при рестарте в простое → воркер пережил родителя: проверить `wait=True` в `shutdown_render_pool()`.

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
- Рендер идёт в одном процессе-воркере (`PDF_RENDER_WORKERS = 1`) — одна страница за раз; страницы PDF, которые рендерятся одновременно, чередуются
- При ошибке конвертации основной запрос не блокируется
