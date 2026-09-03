# Безопасность TlibWebApp

Описание защитных механизмов, настройка и тестирование безопасности.

---

## Реализованные защиты

### 1. Path Traversal Protection

Защита от доступа к файлам вне разрешенных директорий.

**Защищает от:**
- `../` — подъем по директориям
- Абсолютные пути (`/etc/passwd`, `C:\Windows`)
- URL-encoded атаки (`%2E%2E%2F`, double-encoding)
- Обратные слеши `\`
- Null-byte инъекции
- Windows drive / UNC пути

**Реализация (единый канонический валидатор):**
- Источник: `services/security/path_validation.py`
- Функция: `validate_and_resolve_under_base()` — boundary через `Path.relative_to()` (а не строковый `startswith`)
- Функция: `validate_zip_member_path()` — для путей внутри ZIP (без filesystem resolve)

**Применяется во всех роутерах, работающих с путями:**
- `routers/archive_router.py` — имя архива и путь внутри ZIP
- `routers/pdf_router.py` — имя PDF файла
- `routers/cache_router.py` — `archive_name` и `body.path` в `/resolve`
- `routers/png_viewer_router.py` — `dir_path` в `/pages`

**Логирование:**
```
[WARNING] Security: PATH_TRAVERSAL_ATTEMPT ip="192.168.1.100" path="../../../etc/passwd" threat_level="HIGH"
```

---

### 2. Rate Limiting

Ограничение количества запросов с одного IP.

**Параметры (config/security.py):**
```python
RATE_LIMIT_REQUESTS_PER_MINUTE = 300   # Лимит запросов
RATE_LIMIT_CLEANUP_INTERVAL = 300       # Очистка (5 мин)
```

**Исключения:** Статические файлы (`/data/*`, `/js/*`, `/css/*`, `/assets/*`)

**При превышении:**
```http
HTTP/1.1 429 Too Many Requests
Retry-After: 60
```

---

### 3. DoS Protection

Ограничения размеров для защиты от перегрузки.

**Лимиты (config/security.py):**
```python
MAX_ARCHIVE_SIZE = 2 * 1024 * 1024 * 1024  # 2 ГБ - архив
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024     # 2 ГБ - файл в архиве
MAX_FILES_IN_ARCHIVE = 1000                 # Файлов в архиве
UPLOAD_READ_CHUNK_SIZE = 1024 * 1024        # 1 МБ - чанк потоковой записи
UPLOAD_DISK_RESERVE_MULTIPLIER = 2          # Резерв места = MAX_ARCHIVE_SIZE * 2
MAX_COMPRESSION_RATIO = 100                 # Защита от zip-bomb
```

**Потоковая запись upload-файлов (п.1 аудита):**

Принятый файл записывается на диск чанками (`UPLOAD_READ_CHUNK_SIZE`) во временный файл в `data.up/10_up/` (`services/upload/upload_io.py:stream_upload_to_temp`). При превышении `MAX_ARCHIVE_SIZE` в процессе стрима — temp удаляется, возвращается **413**. Пиковое потребление RAM на один upload-запрос ≈ один чанк (1 МБ), независимо от размера файла.

После стрима temp перемещается в финальный путь через `shutil.move` (атомарный rename на том же томе).

**Блокировка загрузок при нехватке места (п.5 аудита):**

Перед приёмом пользовательского файла (`submit`, `submit-edit`) выполняется проверка диска (`disk_allows_upload`). Загрузка блокируется (→ **507**) при выполнении хотя бы одного из условий:
- занятость диска ≥ `DISK_CRIT_PERCENT` (90%), или
- свободно < `MAX_ARCHIVE_SIZE × UPLOAD_DISK_RESERVE_MULTIPLIER` (4 ГБ по умолчанию).

При блокировке **немедленно** отправляется URGENT-алерт `DISK_LOW` (через `send_admin_alert`, троттлинг 30 мин — общий бакет с почасовой проверкой, двойных писем не будет). Алерт срабатывает по **обоим** условиям и по тому, на котором реально измеряется staging (`UPLOAD_STAGING_DIRECTORY`).

`GET /api/upload/status` — read-only эндпоинт, алерт **не** шлёт (может опрашиваться при каждом открытии страницы).

Админские операции (`publish`, `reject`) намеренно **не** блокируются — администратор должен иметь возможность разгрести очередь даже при полном диске.

UI-сторона: при открытии страницы загрузки JS запрашивает `GET /api/upload/status`. При `uploads_enabled=false` форма скрывается и показывается баннер с пояснением. `507` в момент сабмита (гонка) показывает то же сообщение.

---

### 4. Security Headers

HTTP заголовки безопасности для всех ответов.

| Заголовок | Значение | Защита от |
|-----------|----------|-----------|
| X-Content-Type-Options | nosniff | MIME sniffing |
| X-Frame-Options | SAMEORIGIN | Clickjacking |
| X-XSS-Protection | 0 | Легаси-аудитор отключён (убран из браузеров); защита через CSP |
| Referrer-Policy | strict-origin-when-cross-origin | Утечка referrer |
| Strict-Transport-Security | max-age=31536000 | Downgrade атак |
| X-Permitted-Cross-Domain-Policies | none | Legacy Flash/PDF атак |
| Content-Security-Policy | default-src 'self'... | XSS, инъекции |

**Настройка CSP (config/security.py):**
```python
CSP_POLICY = "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; ..."
```

**SEO: X-Robots-Tag** (`middlewares/security_headers.py`):

`SecurityHeadersMiddleware` добавляет `X-Robots-Tag: noindex, nofollow` для:
- `/data/*` и `/data.db/*` — файлы остаются доступными для скачивания, но не индексируются поисковиками;
- служебных HTML-страниц (`/oldscan.html`, `/cloud.html`, `/png-viewer`, `/login.html`, `/upload.html`) — страницы доступны публично, но закрыты от индексации через заголовок (не через `Disallow` в robots.txt, чтобы краулер мог прочитать сам заголовок).

Для `/` с фильтрами (`/?Шифр=...`) `noindex` выставляется непосредственно в `routers/static_router.py::root()`.

---

### 5. HTTP Methods Filtering

**Разрешенные методы:**
- GET, HEAD, OPTIONS — везде
- POST — только для `/api/*`

**Заблокированные:** PUT, DELETE, PATCH, TRACE, CONNECT

---

### 6. Information Disclosure Prevention

- Traceback скрыт от клиента
- API документация отключена (`/docs`, `/redoc`, `/openapi.json`)
- Общие сообщения об ошибках клиенту, детали в логах
- `GET /health` возвращает только статус (`ok`/`error`) и общий текст, без внутренних путей к файлам БД и трассировок; полная диагностика — в `GET /api/admin/status` (только для авторизованных администраторов)

---

### 7. Система авторизации (Magic Link + цифровой код)

Беспарольный вход. При запросе ссылки пользователь получает письмо с двумя способами войти: кликнуть ссылку или ввести 6-значный код. Код позволяет авторизоваться на любом устройстве.

**Поток:**
1. `POST /api/auth/request-link` → удаляет предыдущие записи email, создаёт новую запись, отправляет письмо.
2. Ссылка: `GET /auth/verify?token=...` — атомарный `DELETE...RETURNING` (SQLite 3.35+), одноразово.
3. Код: `POST /api/auth/verify-code` — лимит 5 попыток; при достижении запись удаляется.
4. Любой путь → `create_session()` → cookie `session_token` (httponly, samesite=strict, secure из env).

**Хранение токенов:**
- Таблица `magic_links`: `token_hash`, `code_hash`, `email`, `expires_at`, `attempts`.
- Таблица `sessions`: `token_hash`, `user_id`, `expires_at`, `ip`.
- В БД — только SHA-256 хеши, raw-значения нигде не сохраняются.

**TTL и лимиты (config/security.py):**
- Magic link + код: 15 минут (`AUTH_MAGIC_LINK_TTL`).
- Пер-email rate limit: 1 запрос / 60 сек (`AUTH_MAGIC_LINK_RATE`).
- Пер-IP троттлинг request-link: 5 запросов / 10 мин с одного IP (`AUTH_REQUEST_LINK_IP_MAX`, `AUTH_REQUEST_LINK_IP_WINDOW`); in-memory, сбрасывается при рестарте.
- Дневной лимит исходящих писем: 400/сутки UTC (`AUTH_EMAIL_DAILY_CAP`); персистентно в `app_settings` (`auth.db`). При исчерпании — WARNING в `critical.log` с `event_type=EMAIL_QUOTA` + немедленный **URGENT**-алерт администратору (письмо, троттлинг 30 мин). Плитка «Email Quota» появляется в панели «Безопасность» (`/admin`).
- Лимит попыток кода: 5 (`AUTH_CODE_MAX_ATTEMPTS`), длина кода: 6 цифр (`AUTH_CODE_LENGTH`).
- Сессия: 1 год (`AUTH_SESSION_MAX_AGE`).

**Ответы 429 request-link** содержат структурированные поля для UI:
```json
{"error": "<текст с таймингом>", "reason": "ip_throttle|email_rate|daily_cap", "retry_after": <сек>}
```
Текст не раскрывает существование email.

**Выход со всех устройств:** `POST /api/auth/logout?all=1` удаляет все сессии пользователя (через `delete_all_sessions_for_token`). Обычный `POST /api/auth/logout` удаляет только текущую. UI: dropdown-меню с двумя пунктами у кнопки «Выйти» на страницах `login.html`, `admin.html`, `upload.html`.

**Защита от open redirect:** `redirect` валидируется дважды (в `request-link` и в `verify`) — должен начинаться с `/` и не содержать `//`.

**Ошибки verify** всегда возвращают `error=expired` / `"Неверный или устаревший код"` — нельзя отличить «не существует» от «истёк».

**Очистка:** фоновая задача `_auth_cleanup_loop` (раз в час) вызывает `cleanup_expired()`.

**Суперадмин:** `ROOT_ADMIN_EMAIL` из `data.secret/.env` — всегда роль `admin`, нельзя отобрать.

> **Реальный IP за reverse-proxy:** на проде Caddy проксирует на uvicorn и пробрасывает
> `X-Forwarded-For`, а uvicorn запускается с `--proxy-headers --forwarded-allow-ips=127.0.0.1`
> ([start_ubuntu_caddy.sh](../../start_ubuntu_caddy.sh)), поэтому `request.client.host` = реальный
> IP клиента (подтверждено на проде — в `app.log` разнообразные публичные адреса). Caddy по
> умолчанию игнорирует поддельный `X-Forwarded-For` от недоверенного источника. Поэтому пер-IP
> троттлинг request-link, общий rate-limit, security-логи и привязка админ-сессии к подсети
> работают по настоящему IP. На тест-сервере через Tailscale Funnel реальный IP приходит на
> TCP-уровне.

---

### 8. Admin Panel Authentication

Доступ к `/admin` и `/api/admin/*` защищён через общую систему авторизации (§7) + проверку роли.

**Настройка (data.secret/.env):**
```
ROOT_ADMIN_EMAIL=admin@example.com
```

**Реализация:**
- Источник: `routers/admin_router.py`, функция `_get_admin_user()`
- Аутентификация через cookie `session_token` (общая система из `services/auth/`)
- Проверка: `role == 'admin'` в таблице `users` ИЛИ `email == ROOT_ADMIN_EMAIL`
- `GET /admin` — страница отдаётся всем (200), JS на клиенте определяет что показывать
- `GET /api/admin/health-brief` — публичный, возвращает только `{overall: "healthy"|"degraded"|"unhealthy"}`
- `GET /api/admin/status`, `GET /api/admin/admins`, `POST /api/admin/grant`, `POST /api/admin/revoke` — только для авторизованных админов (401 без сессии)
- `POST /api/admin/revoke` отказывает в отборе прав у `ROOT_ADMIN_EMAIL`
- Для admin-сессий `get_user_by_session()` дополнительно проверяет совпадение подсети IP (/24 IPv4, /64 IPv6)

---

### 9. Upload Page Authentication

Доступ к странице загрузки отчётов `/upload.html` и API `/api/upload/*` защищён той же системой авторизации (cookie `session_token`).

**Реализация:**
- Источник: `routers/upload_router.py`, функции `_get_current_user()` и `_get_admin_user()`
- `GET /upload.html` — страница отдаётся всем (200), JS на клиенте определяет, что показывать
- До авторизации форма загрузки полностью скрыта; видна только шапка с формой входа
- **Любой авторизованный** пользователь: `GET /api/upload/next-code`, `GET /api/upload/check-code`, `POST /api/upload/submit` (без сессии — 401)
- **Только админ**: `GET /api/upload/list`, `GET /api/upload/item`, `GET /api/upload/file`, `POST /api/upload/publish`, `POST /api/upload/reject` (без админ-сессии — 401)
- `submit` дополнительно валидирует: расширение файла (`zip`/`pdf`), ДопШифр (`^[а-яА-Яa-zA-Z0-9]{1,5}$`), уникальность Шифр-ДопШифр (409 при конфликте), размер файла ≤ `MAX_ARCHIVE_SIZE` (413 при превышении)
- `_find_staged_pair()` сравнивает `f.stem.upper()` при переборе директории — путь к файлу не строится из пользовательского ввода (защита от path traversal в `/file`, `/item`, `/publish`, `/reject`)

**Email-уведомления** (`services/auth/email_service.py`, отправка через `asyncio.to_thread`):
- `send_new_report_notice(...)` — при `submit` всем админам (`get_admin_users()` + `ROOT_ADMIN_EMAIL`)
- `send_report_decision(...)` — загрузившему об итоге рассмотрения. При `reject` отправляется сразу; при `publish` письмо отложено: создаётся маркер `data.up/<ID>.notify`, и письмо шлёт File Watcher (`process_pending_notifications`) после реальной публикации отчёта в `data/`

---

## Настройка

### Изменение Rate Limit

```python
# config/security.py
RATE_LIMIT_REQUESTS_PER_MINUTE = 100  # Строже для production
```

### Изменение лимитов размеров

```python
# config/security.py
MAX_ARCHIVE_SIZE = 2 * 1024 * 1024 * 1024  # 2 ГБ
```

### Блокировка IP через firewall

```bash
sudo ufw deny from 192.168.1.100 to any port 8080
```

---

## Мониторинг

### Файл логов безопасности

Все события в `logs/critical.log`:

```bash
# События безопасности
grep "category=SECURITY" logs/critical.log

# Высокий уровень угрозы
grep "threat_level=\"HIGH\"" logs/critical.log

# Статистика по типам
grep "event_type=" logs/critical.log | sed 's/.*event_type=\([A-Z_]*\).*/\1/' | sort | uniq -c | sort -rn
```

### Типы событий

| Событие | Описание | Уровень |
|---------|----------|---------|
| PATH_TRAVERSAL_ATTEMPT | Попытка обхода директорий | HIGH |
| RATE_LIMIT_EXCEEDED | Превышение лимита запросов | MEDIUM |
| ARCHIVE_SIZE_EXCEEDED | Запрос большого архива | MEDIUM |
| INVALID_REQUEST | Невалидный запрос | LOW |

---

## Тестирование

Безопасность покрыта автоматическими тестами:
- Path Traversal (6+ векторов атак)
- Security Headers (все заголовки)
- Content Security Policy
- XSS Protection
- Error Handling (скрытие traceback)

Запуск и варианты — см. [tests/README.md](../../tests/README.md).

### Ручное тестирование

```bash
# Проверка заголовков
curl -I http://localhost:8080/

# Попытка Path Traversal (должен вернуть 400)
curl -i "http://localhost:8080/api/archive/..%2F..%2Fetc%2Fpasswd/contents"

# Запрещенный метод (должен вернуть 405)
curl -X DELETE http://localhost:8080/
```

---

## Рекомендации

### Обязательно

- Мониторить `logs/critical.log` ежедневно
- Запускать тесты перед деплоем
- Обновлять зависимости
- Использовать HTTPS (Tailscale Funnel или Caddy)

### Для production

- Уменьшить `RATE_LIMIT_REQUESTS_PER_MINUTE` до 100
- Настроить автоматические алерты на `critical.log`
- Ограничить доступ через firewall
- Настроить централизованные логи (Graylog, ELK)

---

**См. также:** [Логирование](LOGGING.md)
