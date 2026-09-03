# База данных TlibWebApp

Структура SQLite базы данных, автообновление и кэширование.

---

## Файлы

| Файл | Назначение |
|------|------------|
| `data.db/tlib.db` | Рабочая база данных |
| `tlib-new.db` | Триггер автообновления |
| `data.old/tlib-*.db` | Бэкапы с timestamp |
| `data.db/tlib.xlsx` | Экспорт в Excel |

---

## Структура таблицы tlib

| Поле | Тип | Описание |
|------|-----|----------|
| Шифр | INTEGER | Основной идентификатор |
| ДопШифр | TEXT | Дополнительный шифр (а, б, в...) |
| Маршрут | TEXT | Название маршрута |
| РайонОбщий | TEXT | Общий район |
| Район | TEXT | Конкретный район |
| Автор | TEXT | Автор отчета |
| Город | TEXT | Город автора |
| Тип | TEXT | Тип похода (пеший, водный...) |
| ТипСудна | TEXT | Тип судна (для водных) |
| КатегорияС | TEXT | Категория начальная |
| КатегорияПо | TEXT | Категория итоговая |
| Год | INTEGER | Год похода |
| МесяцС | INTEGER | Месяц начала (1-12) |
| МесяцПо | INTEGER | Месяц окончания (1-12) |
| Комментарии | TEXT | Примечания |
| РазмерАрхива | INTEGER | Размер в байтах |
| ТипФайла | TEXT | "zip" или "pdf" |

---

## Автообновление

### Процедура (ручная замена)

1. Скопировать новую БД:
   ```bash
   cp new_database.db tlib-new.db
   ```

2. Database Watcher автоматически (каждые 5 сек):
   - Валидация SQLite файла
   - Создание бэкапа: `tlib-20251209143052.db`
   - Атомарная замена `tlib.db`
   - Обновление кэша
   - Удаление старых бэкапов (>30 дней)

### Принудительная пересборка из JSON (reindex)

Чтобы пересобрать `tlib.db` из всех JSON-файлов в `data/` без ручного вмешательства:

```bash
touch data.up/20_go/reindex.now
# File Watcher пересоберёт БД в течение минуты
# (reindex.* обрабатывается без задержки stability-window)
```

Расширение не имеет значения: `reindex.txt`, `reindex.trigger` и т.д. — любое.

Применяется когда:
- Файлы в `data/` изменились вне пайплайна (ручная правка, восстановление из бэкапа)
- Нужно убедиться в консистентности БД

> JSON-карточки при пересборке читаются через `services/json_io.py` (`read_json`, кодек `utf-8-sig`), поэтому отчёты с UTF-8 BOM в начале файла обрабатываются корректно, без предварительной конвертации.

### При ошибке

- Невалидный файл удаляется автоматически
- Текущая БД остается без изменений
- Ошибка записывается в логи

---

## Инвариант: data/ — единственный источник истины

`data/` — единственный источник истины для содержимого БД. Любая мутация (INSERT/UPDATE/DELETE/REINDEX) обязана менять файлы в `data/` и пересобирать `tlib-new.db` через единый канал:

```
изменение data/
  → generate_final_database_check([…])   # пересборка tlib-new.db
  → publish_database()                   # tlib-new.db → assets/tlib-new.db
  → database_watcher_task                # обнаруживает триггер
  → perform_database_update()            # атомарный swap + refresh app.state
```

Это обеспечивает, что `app.state.*` и БД всегда соответствуют друг другу. **Запрещается** изменять `assets/tlib.db` напрямую (SQL INSERT/UPDATE/DELETE в обход пайплайна) — такие изменения не будут отражены в `app.state`.

---

## Кэширование справочников

`app.state.*` — производный кэш от `assets/tlib.db`. Обновляется **только** внутри `perform_database_update()` (один раз при старте и при каждом атомарном swap). Нигде больше.

```python
app.state.dopshifr_list          # Список ДопШифр
app.state.raion_obshiy_list      # Список РайонОбщий
app.state.tip_list               # Список Тип
app.state.kategoria_s_list       # Список КатегорияС
app.state.kategoria_po_list      # Список КатегорияПо
app.state.kategoria_unified_list # Объединённый список категорий
app.state.reports_count          # Количество отчётов в БД
app.state.redirect_table         # Таблица редиректов
app.state.reference_version      # ISO-timestamp последнего обновления (фронт поллит)
```

**Доступ в роутерах:**
```python
request.app.state.tip_list
```

**Обновление кэша:** Автоматически через `perform_database_update()` при каждой замене БД. Фронтенд узнаёт об обновлении через `reference_version` (поллинг `/api/reference-version` в `referenceListsService.js`).

---

## Бэкапы

### Автоматические

- При обновлении БД: `data.old/tlib-YYYYMMDDHHMMSS.db`
- При обновлении файлов: `data.old/` с timestamp

### Ручной откат

```bash
# Посмотреть бэкапы
ls -la data.old/tlib-*.db

# Восстановить
cp data.old/tlib-20251205093000.db tlib-new.db
```

### Очистка

```bash
# Удалить старые бэкапы (оставить последние 10)
ls -t data.old/tlib-*.db | tail -n +11 | xargs rm -f
```

---

## Экспорт в XLSX

При изменениях через File Watcher автоматически обновляется `data.db/tlib.xlsx`.

Файл содержит все колонки таблицы `tlib` плюс два вычисляемых столбца в конце:

| Столбец | Содержание |
|---------|------------|
| **tLib** | Кликабельная ссылка на страницу отчёта: `{SITE_URL}/?{Шифр}-{ДопШифр}`. Пустая, если `SITE_URL` не задан в `.env`. |
| **pCloud** | Кликабельная ссылка на файл отчёта в облачном зеркале. Пустая, если у отчёта нет файла (`РазмерАрхива = 0` или `ТипФайла` пуст). |

Базовый URL зеркала задаётся константой `PCLOUD_DATA_BASE_URL` в `config/database.py`.

**Ручной экспорт:**
```python
from services.database.export_utils import export_database_to_xlsx
export_database_to_xlsx()
```

---

## Конфигурация

**config/database.py:**
```python
DATABASE_PATH = "data.db/tlib.db"
DATABASE_NEW_FILE = "tlib-new.db"
DATABASE_CHECK_INTERVAL = 5           # Секунды
BACKUP_DIRECTORY = "data.old"
DATABASE_BACKUP_PREFIX = "tlib"
```

---

## SQL запросы

### Структура поиска

Модуль: `services/database/query_builder.py`

```python
def build_search_query(params: dict) -> tuple[str, list]:
    # Возвращает SQL запрос и параметры
```

### Фильтры

Модуль: `services/database/query_filters.py`

- Поиск по тексту (LIKE)
- Диапазоны годов
- Множественный выбор (IN)
- Полнотекстовый поиск

---

## Полезные команды

```bash
# Количество записей
sqlite3 data.db/tlib.db "SELECT COUNT(*) FROM tlib"

# Последние добавленные
sqlite3 data.db/tlib.db "SELECT Шифр, ДопШифр, Маршрут FROM tlib ORDER BY rowid DESC LIMIT 10"

# Поиск по шифру
sqlite3 data.db/tlib.db "SELECT * FROM tlib WHERE Шифр = 12345"

# Размер БД
ls -lh data.db/tlib.db
```

---

**См. также:** [File Watcher](FILE_WATCHER.md), [API](API.md)
