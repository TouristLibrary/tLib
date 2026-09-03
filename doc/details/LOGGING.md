# Логирование TlibWebApp

Структура логов, формат записей и анализ событий.

---

## Файлы логов

### logs/app.log

**Назначение:** Основные события приложения  
**Уровень:** INFO и выше  
**Ротация:** 20 МБ, хранится 5 файлов

### logs/debug.log

**Назначение:** Детальная отладка  
**Уровень:** DEBUG и выше  
**Ротация:** 50 МБ, хранится 3 файла  
**Включение:** константа `LOG_DEBUG_LEVEL` в `config/app.py`; по умолчанию `"OFF"` — файл не пишется. Переопределяется переменной окружения `LOG_DEBUG_LEVEL` из `data.secret/.env` (например `LOG_DEBUG_LEVEL=DEBUG` на тестовом стенде), поэтому включать отладку на сервере правкой кода не нужно.

### logs/critical.log

**Назначение:** Критичные события, требующие внимания  
**Уровень:** WARNING и выше  
**Ротация:** 10 МБ, хранится 10 файлов

**Содержит:**
- Критичные ошибки (ERROR, CRITICAL)
- События безопасности (атаки, подозрительная активность)
- Важные предупреждения

---

## Формат записей

```
[timestamp] LEVEL [req_id] function:line | key=value key="quoted value" ...
```

**Пример:**
```
[2025-11-22 20:50:41.123] INFO [8a5b2c3d] startup_event:1218 | msg="FastAPI запущен" port=8080
```

**Компоненты:**
- `[timestamp]` — UTC с миллисекундами
- `LEVEL` — DEBUG, INFO, WARNING, ERROR, CRITICAL
- `[req_id]` — первые 8 символов UUID запроса (`--------` для событий вне HTTP)
- `function:line` — место в коде
- `key=value` — структурированные данные

**Программный разбор:** `key=value`-поля (в т.ч. значения в кавычках с экранированием) извлекаются единой функцией `parse_logfmt_fields()` из `logging_config.py`. Её используют панель «Безопасность» в `/admin` (`services/admin/status_service.py`) и email-дайджест (`services/alerts/digest.py`).

---

## Request ID

Каждому HTTP запросу присваивается уникальный UUID.

- В логах: `[req_id]` — первые 8 символов
- Полный UUID: поле `request_id=...`
- Передается клиенту в заголовке: `X-Request-ID`

**Связывание логов одного запроса:**
```bash
grep "\[8a5b2c3d\]" logs/*.log
```

---

## Системные логи

Создаются shell-скриптами при автозапуске:

| Файл | Источник | Содержимое |
|------|----------|------------|
| logs/out.log | stdout start_ubuntu*.sh | Вывод скрипта запуска |
| logs/err.log | stderr | uvicorn логи + Python логи |
| logs/funnel.log | start_ubuntu_tailscale_funnel.sh | Настройка Tailscale Funnel (только для Funnel-режима) |

**Можно удалять** — пересоздаются при следующем запуске.

---

## Уровни логирования

| Уровень | app.log | debug.log | critical.log |
|---------|---------|-----------|--------------|
| DEBUG | - | + | - |
| INFO | + | + | - |
| WARNING | + | + | + |
| ERROR | + | + | + |
| CRITICAL | + | + | + |

---

## Анализ логов

### Мониторинг в реальном времени

```bash
tail -f logs/critical.log
tail -f logs/app.log
```

### Поиск ошибок

```bash
# Все ошибки
grep -E "ERROR|CRITICAL" logs/app.log

# Ошибки за сегодня
grep "$(date +%Y-%m-%d)" logs/app.log | grep ERROR
```

### Поиск по Request ID

```bash
# Все логи одного запроса
grep "\[8a5b2c3d\]" logs/*.log

# С контекстом
grep -C 5 "\[8a5b2c3d\]" logs/app.log
```

### Анализ безопасности

```bash
# Все события безопасности
grep "category=SECURITY" logs/critical.log

# Извлечь IP адреса атакующих
grep "category=SECURITY" logs/critical.log | grep -oP 'ip="\K[^"]*' | sort | uniq -c | sort -rn
```

---

## Ротация

Логи ротируются автоматически при достижении максимального размера:

- `app.log` → `app.log.1`, `app.log.2`, ... (до 5 файлов)
- `debug.log` → `debug.log.1`, ... (до 3 файлов)
- `critical.log` → `critical.log.1`, ... (до 10 файлов)

**Максимум на диске:** ~350 МБ

---

## Категории событий

### SECURITY

События безопасности с дополнительными полями:
- `threat_level` — HIGH, MEDIUM, LOW
- `ip` — IP адрес клиента
- `event_type` — тип события

### CRITICAL_ERROR

Критичные ошибки приложения:
- `error_type` — тип ошибки
- `path` — путь к проблемному ресурсу

---

## Рекомендации

### Для администраторов

1. Проверяйте `critical.log` ежедневно
2. Настройте алерты на `category=SECURITY` и `threat_level="HIGH"`
3. Архивируйте старые .1, .2, ... файлы периодически

### Для разработчиков

1. Используйте `debug.log` для отладки
2. Добавляйте структурированные данные как `key=value`
3. Используйте `app_logger` из `logging_config.py`

---

**См. также:** [Безопасность](SECURITY.md)
