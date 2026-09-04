# Тесты TlibWebApp

## Требования

- Node.js 18+ (для Playwright E2E)
- Python 3.10+ (для API и unit-тестов)
- Изолированный venv в корне проекта (рекомендуется):

```powershell
# из корня проекта
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r tests\requirements-test.txt
```

```powershell
# из tests/ (E2E)
npm install
```

---

## E2E-тесты (Playwright)

> Запуск из `tests/` — там находится `package.json` и `playwright.config.js`.

Требуют запущенного сервера (`BASE_URL`).

```powershell
# Все E2E-тесты (~3 мин):
$env:BASE_URL="https://yourdomain.com"; npm run test:predeploy
```

**По тегам:**

| Команда | Что запускает | Время |
|---------|--------------|-------|
| `npm run test:tagged:critical` | Только критические (@critical) | ~30 сек |
| `npm run test:tagged:smoke` | Smoke-набор (@smoke) | ~1 мин |
| `npm run test:tagged:security` | Безопасность (@security) | ~1 мин |

**По отдельному файлу:**

```powershell
npm run test:critical
npm run test:security
npm run test:search
npm run test:report
npm run test:navigation
npm run test:responsive
npm run test:performance
```

**Просмотр результатов и trace:**

```powershell
npm run test:e2e:report
# Скриншоты и trace — в папке test-results/
```

---

## API-тесты (pytest + httpx)

> Запуск из `tests/` — тесты обращаются к серверу по HTTP, локальных импортов нет.

Требуют запущенного сервера (`BASE_URL`). Проверяют HTTP-контракт без браузера.

```powershell
# из tests/
$env:BASE_URL="https://yourdomain.com"; python -m pytest api -v
```

**По отдельному файлу:**

```powershell
$env:BASE_URL="https://yourdomain.com"
python -m pytest api/test_health.py -v
python -m pytest api/test_search.py -v
python -m pytest api/test_security_headers.py -v
python -m pytest api/test_error_responses.py -v
python -m pytest api/test_rate_limit.py -v
python -m pytest api/test_reference_lists.py -v
python -m pytest api/test_upload_auth_smoke.py -v
python -m pytest api/test_archive.py -v
python -m pytest api/test_cache_png_paths.py -v
```

---

## Unit- и integration-тесты (pytest)

> Запуск из **корня проекта** — тесты импортируют модули приложения (`services.*`, `routers.*`).
> Требуют активированного `.venv` (см. выше).

Не требуют запущенного сервера.

> **Важно:** integration-тесты зависят от `fastapi` и `python-multipart`, которые входят
> в основной `requirements.txt`. При установке окружения устанавливайте **оба** файла:
> `pip install -r requirements.txt -r tests\requirements-test.txt`

```powershell
# из корня проекта
python -m pytest tests/unit -v
python -m pytest tests/integration -v   # auth/upload/admin in-process + сценарии File Watcher pipeline, без почты и сервера
python -m pytest tests/unit tests/integration -v
```

---

## Ручная проверка SMTP

Скрипт `tools/check_smtp_gmail.py` позволяет вручную проверить корректность SMTP-настроек Gmail.
Это не pytest-тест, а интерактивная утилита:

```powershell
# из корня проекта
python tools/check_smtp_gmail.py
```

---

## MCP-тестирование (интерактивно, через чат-сессию)

> Не зависит от рабочей директории — выполняется AI-агентом через браузер.

Требуют запущенного сервера и AI-агента с MCP Playwright.

Файл [`smoke-test-mcp.md`](smoke-test-mcp.md) — сценарий из 15 тестов (Visual / UX / Exploratory / Accessibility), которые выполняет AI-агент через MCP Playwright.

**Когда использовать:** когда нужна визуальная проверка качества рендеринга, UX-оценка, или тестирование нестандартного поведения — то, что автоматизированные тесты не умеют (пиксели, читаемость, субъективное восприятие).

**Как запустить:**

1. Откройте чат с AI-агентом (Cursor или аналог с MCP Playwright)
2. Прикрепите файл `tests/smoke-test-mcp.md` к сообщению
3. Напишите: _«Выполни все тесты из прикреплённого файла»_

Агент последовательно пройдёт все 15 тестов и вернёт итоговый отчёт.
