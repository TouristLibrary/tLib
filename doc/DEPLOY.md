# Развертывание TlibWebApp

Инструкция по установке и запуску приложения на Ubuntu Server.

## Предварительный шаг

Перед установкой приложения подготовьте сервер по инструкции [PREDEPLOY.md](PREDEPLOY.md) (создание ВМ, ОС, SSH, firewall).


## Установка (5 минут)

### Шаг 1: Подготовка директории на сервере

**Рекомендуемое расположение:** `/opt/TlibWebApp`

Подключитесь к серверу по SSH и создайте директорию:

```bash
ssh tlib-admin@server-ip

sudo mkdir -p /opt/TlibWebApp && sudo chown $USER:$USER /opt/TlibWebApp
```

> **Примечание:** Директорию в `/opt/` нельзя создать без sudo. Если вы используете WinSCP и видите ошибку "Permission denied", сначала выполните команды выше через SSH терминал (в WinSCP: Ctrl+T).

**Альтернативные варианты:**
- `/srv/TlibWebApp` — также стандартное место для сервисов
- `/home/tlib-admin/TlibWebApp` — для тестирования (не требует sudo)

**Отдельные диски для данных (если подготовлены в [PREDEPLOY.md](PREDEPLOY.md)):**

Если на сервере настроены отдельные диски (например, быстрый SSD для БД/кеша и медленный для архивов), создайте симлинки до копирования файлов:

```bash
# Быстрый диск — БД и кеш
for d in data.db data.cache; do sudo ln -s /mnt/fast/$d /opt/TlibWebApp/$d; done

# Медленный диск — архивы, загрузки, бэкапы
for d in data data.up data.new data.old; do sudo ln -s /mnt/slow/$d /opt/TlibWebApp/$d; done
```

> Симлинки прозрачны для приложения — оно работает с привычными путями (`data/`, `data.db/` и т.д.), а данные физически лежат на нужных дисках. Замените `/mnt/fast` и `/mnt/slow` на ваши реальные точки монтирования.

### Шаг 2: Копирование файлов на сервер

Скопируйте на сервер следующие директории и файлы:

| Тип | Что копировать |
|-----|----------------|
| Бэкенд | `config/`, `middlewares/`, `routers/`, `services/` |
| Фронтенд | `js/`, `css/`, `svg/` |
| Ресурсы | `assets/` |
| Скрипты установки | `tools/` (содержит `install_ubuntu*.sh`) |
| Скрипты запуска | `start_ubuntu*.sh` (в корне проекта) |
| Корневые файлы | `app.py`, `logging_config.py`, `requirements.txt`, `index.html`, `about.html`, `admin.html`, `login.html`, `upload.html`, `cloud.html`, `oldscan.html`, `png-viewer.html` |
| Документация | `doc/` (опционально) |

**Через SCP:**
```bash
# Директории
scp -r config middlewares routers services js css svg assets tools \
  tlib-admin@server-ip:/opt/TlibWebApp/

# Корневые файлы
scp app.py logging_config.py requirements.txt \
  index.html about.html admin.html login.html upload.html cloud.html oldscan.html png-viewer.html \
  start_ubuntu*.sh \
  tlib-admin@server-ip:/opt/TlibWebApp/
```

**Через WinSCP:** Подключитесь по SFTP, перейдите в `/opt/TlibWebApp/`, скопируйте перечисленные выше директории и файлы.

### Шаг 3: Настройка конфигурации

Отредактируйте `config/app.py` — укажите путь к архивам:

```bash
nano /opt/TlibWebApp/config/app.py
```

Замените:

```python
DATA_DIRECTORY: str = "/mnt/usb_drive/Tlib/data"
```

На:

```python
DATA_DIRECTORY: str = "data"
```

> Относительный путь `"data"` указывает на директорию (или симлинк) `data/` внутри `/opt/TlibWebApp/`.

**Пути к кешу и базе данных:** по умолчанию тоже относительные и работают с симлинками из Шага 1. Если раскладка дисков отличается — скорректируйте:

| Константа | Файл | По умолчанию |
|-----------|------|--------------|
| `CACHE_DIRECTORY` | `config/app.py` | `"data.cache"` |
| `DATABASE_PATH` | `config/database.py` | `"data.db/tlib.db"` |

**Размер кеша:** в `config/cache.py` значение `MAX_CACHE_SIZE` по умолчанию — 150 ГБ. Убедитесь, что на диске с кешем достаточно места, или уменьшите:

```python
MAX_CACHE_SIZE: int = 150 * 1024 * 1024 * 1024  # 150 ГБ
```

**Admin-панель:** настройте доступ в `data.secret/.env`:

```bash
nano /opt/TlibWebApp/data.secret/.env
```

Укажите email суперадмина, URL сайта и флаг безопасного cookie (если ещё не заданы):

```
ROOT_ADMIN_EMAIL=admin@example.com
SITE_URL=https://yourdomain.com
AUTH_COOKIE_SECURE=true
```

`ROOT_ADMIN_EMAIL` — всегда имеет права админа, нельзя отобрать через UI. Первый вход: откройте `https://yourdomain.com/admin`, введите email, кликните magic link из письма. Остальным админам права выдаются через секцию управления внизу страницы `/admin`.

`SITE_URL` также определяет префикс `[<домен>]` в теме всех исходящих писем (алерты, дайджест, уведомления о загрузке/публикации/удалении отчётов) — см. `MAIL_SUBJECT_PREFIX` в `config/security.py` и [ALERTS.md](details/ALERTS.md#структура-письма-алерта). Если `SITE_URL` не задан, тема начинается с заглушки `[tLib-unknown-host]`.

`AUTH_COOKIE_SECURE=true` — устанавливает флаг `Secure` на session-cookie, запрещая браузеру отправлять его по незашифрованному HTTP. Устанавливайте только при деплое за HTTPS (Caddy или Tailscale Funnel). Для локального HTTP-режима (`start_ubuntu_local.sh`) оставьте значение `false` (или не задавайте — по умолчанию `false`).

**IP-binding для администраторов:** сессия администратора привязывается к подсети (`/24` IPv4, `/64` IPv6), в которой был выполнен вход. Запрос из другой подсети возвращает 401 и требует повторного входа по magic link. Смена адреса внутри подсети провайдера сессию не сбрасывает. Ограничения: при использовании Tailscale Funnel (`start_ubuntu_tailscale_funnel.sh`) binding неэффективен, так как uvicorn запускается без `--proxy-headers` и все клиенты видны с одного адреса прокси.

**Production-режим:** отключите отладочное логирование.

`LOG_DEBUG_LEVEL` в `config/app.py` по умолчанию `"OFF"` и переопределяется переменной окружения `LOG_DEBUG_LEVEL` из `data.secret/.env` — так можно включить `debug.log` на тестовом стенде (`LOG_DEBUG_LEVEL=DEBUG`) без правки кода. На production `.env` эту переменную не задавайте — сработает дефолт `"OFF"`.

В `js/config/constants.js` установите:

```javascript
export const DEBUG_MODE = false;
```

> `LOG_DEBUG_LEVEL = "OFF"` отключает запись серверного `debug.log`. `DEBUG_MODE = false` подавляет `console.log`/`console.debug` в браузере у пользователей.

### Шаг 4: Установка зависимостей

```bash
cd /opt/TlibWebApp && chmod +x tools/*.sh start_ubuntu*.sh
```

Выберите режим развертывания и запустите соответствующий скрипт:

| Режим | Команда | Подходит для |
|-------|---------|--------------|
| **A. Локальный** | `./tools/install_ubuntu_local.sh` | Внутренняя сеть, тестирование |
| **B. Tailscale Funnel** | `./tools/install_ubuntu_tailscale_funnel.sh` | Публичный HTTPS без настройки DNS |
| **C. Caddy** | `./tools/install_ubuntu_caddy.sh домен email` | Production с собственным доменом |

**Предварительно для Caddy (вариант C):**  
Настройте DNS запись вашего домена на IP сервера. См. [Инструкция по настройке DNS](details/DNS_SETUP.md).

**После установки для Tailscale Funnel (вариант B):**
```bash
sudo tailscale up && sudo tailscale set --operator=$USER
```

> **Автоматически создаваемые директории:**
> Скрипты установки и приложение автоматически создают все необходимые рабочие директории:
> - `data/`, `css/`, `js/`, `assets/`, `logs/` — при установке
> - `data.up/`, `data.new/`, `data.old/`, `data.up/10_up/`, `data.up/20_go/`, `data.up/30_processing/`, `data.up/40_error/` — при запуске приложения
> - `data.cache/`, `data.db/` — при первом использовании
>
> Если симлинки на отдельные диски уже созданы (Шаг 1), скрипты корректно используют их — директории создаются на целевых дисках. Создавать их вручную **не нужно**.

### Шаг 5: Запуск приложения

Запустите соответствующий скрипт:

```bash
# Для локального режима
./start_ubuntu_local.sh

# Для Tailscale Funnel
./start_ubuntu_tailscale_funnel.sh

# Для Caddy
./start_ubuntu_caddy.sh

# Или используйте универсальный wrapper (по умолчанию Tailscale Funnel)
./start_ubuntu.sh
```

**Доступ к приложению:**
- Локальный режим: `http://IP-СЕРВЕРА:8080/`
- Tailscale Funnel: `https://hostname.tailnet-name.ts.net/`
- Caddy: `https://yourdomain.com/`

---

## Проверка работоспособности

1. Откройте адрес приложения в браузере (`https://yourdomain.com` или Tailscale URL)
2. Должна загрузиться форма поиска
3. Введите любые параметры и нажмите "Найти"
4. Кликните на шифр в результатах — должен открыться список файлов архива

### Автоматическая верификация

На **рабочей машине** (не на сервере) запустите тесты — см. [tests/README.md](../tests/README.md).

---

## Автозапуск при перезагрузке

### systemd (рекомендуется)

```bash
sudo nano /etc/systemd/system/tlibapp.service
```

**Для локального режима:**
```ini
[Unit]
Description=TlibWebApp FastAPI Server (Local)
After=network.target

[Service]
Type=simple
User=tlib-admin
WorkingDirectory=/opt/TlibWebApp
ExecStart=/opt/TlibWebApp/start_ubuntu_local.sh
Restart=always
RestartSec=10
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
```

> **`TimeoutStopSec=15`** — страховка на случай, если graceful shutdown залипнет (например, на `to_thread`-операциях File Watcher), а не основной механизм остановки. Значение 15, а не меньше — чтобы не обрезать штатную отмену фоновых задач в `lifespan` при обычном рестарте.

**Для Tailscale Funnel:**

> **Funnel настраивается один раз, отдельно от сервиса.** Конфигурация Tailscale Funnel персистентна — она хранится в состоянии `tailscaled` и переживает перезагрузки; сам `tailscaled` восстанавливает её после каждого старта, когда будет готов. Поэтому **не** вызывайте `tailscale funnel` из стартового/стоп-скрипта сервиса:
> - на старте сразу после загрузки `tailscaled` может быть ещё в состоянии `Starting` → `tailscale funnel --bg` вернёт ошибку, и до следующей перезагрузки сайт останется без публичного доступа (сам процесс приложения при этом поднимется нормально);
> - на остановке `tailscale funnel reset` может зависнуть, и systemd будет ждать `TimeoutStopSec` (по умолчанию 90 с) перед SIGKILL — перезагрузка/рестарт становятся медленными.
>
> Правильный порядок: включить Funnel **один раз** командой `tailscale funnel --bg 8080`, а юнит сервиса указывает на `start_ubuntu_local.sh` (тот же порт и хост, но без вызовов `tailscale`):

```ini
[Unit]
Description=TlibWebApp FastAPI Server (Tailscale Funnel)
After=network-online.target tailscaled.service
Wants=network-online.target tailscaled.service

[Service]
Type=simple
User=tlib-admin
WorkingDirectory=/opt/TlibWebApp
ExecStart=/opt/TlibWebApp/start_ubuntu_local.sh
Restart=always
RestartSec=10
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
```

> **Почему `Wants=`, а не `Requires=` для tailscaled:** сайт должен подниматься даже если Tailscale временно недоступен — без него теряется только публичный доступ, локальный/LAN остаётся рабочим. С `Requires=` остановка/сбой tailscaled остановил бы и приложение.
>
> `start_ubuntu_tailscale_funnel.sh` при этом не становится лишним — используйте его для ручных/интерактивных запусков (например, при первичной настройке Funnel) и для смены порта/hostname; под systemd постоянно работает не он.

> **Диск данных на отдельном носителе:** если `data/` (или `data.db`/`data.cache`) — симлинк на отдельный диск (USB, сетевой том), который монтируется не сразу при загрузке, добавьте в секцию `[Unit]` директиву `RequiresMountsFor=/путь/к/точке/монтирования` (например, `RequiresMountsFor=/mnt/usb_drive`). Она заставляет systemd дождаться монтирования перед стартом сервиса — без неё возможна гонка, когда `ExecStart` запускается раньше, чем диск примонтирован, и падает на создании рабочих директорий.

**Для Caddy:**
```ini
[Unit]
Description=TlibWebApp FastAPI Server (Caddy)
After=network.target caddy.service
Requires=caddy.service

[Service]
Type=simple
User=tlib-admin
WorkingDirectory=/opt/TlibWebApp
ExecStart=/opt/TlibWebApp/start_ubuntu_caddy.sh
Restart=always
RestartSec=10
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
```

Активация:
```bash
sudo systemctl daemon-reload && sudo systemctl enable tlibapp && sudo systemctl start tlibapp
```

---

## Публичный доступ (HTTPS)

### Tailscale Funnel (простой вариант)

Для публикации приложения в интернет без настройки портов и SSL:

1. Используйте `./tools/install_ubuntu_tailscale_funnel.sh` при установке
2. Настройте Tailscale: `sudo tailscale up` и `sudo tailscale set --operator=$USER`
3. Запустите: `./start_ubuntu_tailscale_funnel.sh`

Результат: публичный HTTPS адрес вида `https://hostname.tailnet-name.ts.net`

#### Watchdog публичной доступности (рекомендуется для Funnel)

Известный класс багов Tailscale: узел может сменить «домашний» DERP-релей (например, из-за разницы в задержке до соседних регионов всего в 1 мс), при этом пиры и ingress-серверы Funnel продолжают слать трафик на старый релей. Публичный сайт и SSH через tailnet перестают быть доступны на неопределённое время (наблюдалось — почти 2 часа), хотя сам сервер и приложение полностью исправны. Диагностический признак: `tailscale funnel status` по-прежнему пишет «Funnel on», `tailscale status --json` не показывает никаких `Health`-предупреждений, `localhost:8080` отвечает 200 — но публичный URL не открывается (в браузере обычно `PR_END_OF_FILE_ERROR`). Лечится только перезапуском `tailscaled` или перезагрузкой сервера; способа закрепить домашний DERP штатными средствами Tailscale нет.

Обычная проверка `curl https://hostname.tailnet-name.ts.net/` **не годится** для обнаружения — с самого сервера (и с любой машины в том же tailnet) имя резолвится через MagicDNS в адрес tailnet (`100.x.x.x`) и всегда отвечает 200, минуя реальный публичный ingress. Проверять нужно через собственный DNS-over-HTTPS, чтобы имя резолвилось так же, как для внешнего браузера:

```bash
curl --doh-url https://dns.google/dns-query https://hostname.tailnet-name.ts.net/health
```

Скрипт `tools/tlib-net-watchdog.sh` автоматизирует эту проверку: если локальное приложение отвечает, а публичный путь — нет (после двух попыток с паузой), перезапускает `tailscaled`. Установка (на сервере, после копирования `tools/`):

```bash
chmod +x /opt/TlibWebApp/tools/tlib-net-watchdog.sh

sudo tee /etc/systemd/system/tlib-net-watchdog.service >/dev/null <<'EOF'
[Unit]
Description=Watchdog публичной доступности TlibWebApp через Tailscale Funnel
After=network-online.target tailscaled.service

[Service]
Type=oneshot
ExecStart=/opt/TlibWebApp/tools/tlib-net-watchdog.sh https://hostname.tailnet-name.ts.net/health
EOF

sudo tee /etc/systemd/system/tlib-net-watchdog.timer >/dev/null <<'EOF'
[Unit]
Description=Периодическая проверка публичной доступности TlibWebApp

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload && sudo systemctl enable --now tlib-net-watchdog.timer
```

Проверка: `journalctl -u tlib-net-watchdog -n 10 --no-pager` — без строки о перезапуске означает, что публичный путь сейчас живой; `systemctl list-timers tlib-net-watchdog` показывает следующий запуск.

> Для варианта с Caddy этот watchdog не нужен — там нет Tailscale Funnel и связанного с ним класса сбоев.

### Caddy + Let's Encrypt (с доменом)

Для развертывания с собственным доменом:

1. Настройте DNS запись (A или AAAA) для вашего домена на IP сервера
2. Используйте `./tools/install_ubuntu_caddy.sh yourdomain.com admin@yourdomain.com`
3. Запустите: `./start_ubuntu_caddy.sh`

Результат: автоматический HTTPS через Let's Encrypt на вашем домене

**Управление Caddy:**
```bash
sudo systemctl status caddy    # Статус
sudo systemctl restart caddy   # Перезапуск
sudo journalctl -u caddy -f    # Логи
```

---

## Управление

| Действие | Локальный | Tailscale Funnel | Caddy |
|----------|-----------|------------------|-------|
| Запуск | `./start_ubuntu_local.sh` | `./start_ubuntu_tailscale_funnel.sh` | `./start_ubuntu_caddy.sh` |
| Остановка | `Ctrl+C` или `pkill -f uvicorn` | `Ctrl+C` | `Ctrl+C` |
| Статус (systemd) | `sudo systemctl status tlibapp` | `sudo systemctl status tlibapp` | `sudo systemctl status tlibapp` и `sudo systemctl status caddy` |
| Логи приложения | `tail -f logs/app.log` | `tail -f logs/app.log` и `tail -f logs/funnel.log` | `tail -f logs/app.log` |
| Логи веб-сервера | - | - | `sudo journalctl -u caddy -f` |

---

## Troubleshooting

### "python: command not found"
```bash
python3 --version
# Используйте python3 вместо python
```

### "Address already in use"
```bash
# Найти процесс на порту 8080
sudo lsof -i :8080
# Завершить процесс
sudo kill -9 PID
```

### Не открывается из сети

Относится только к варианту `start_ubuntu_local.sh` (прямой доступ из LAN, `HOST=0.0.0.0`).
Для Caddy и Tailscale Funnel наружу смотрит прокси (`:443`), а uvicorn слушает `127.0.0.1:8080` — это норма, а не проблема.

```bash
# Проверить firewall
sudo ufw status
sudo ufw allow 8080/tcp

# Проверить что сервер слушает все интерфейсы
netstat -tulpn | grep :8080
# Для start_ubuntu_local.sh должно быть 0.0.0.0:8080.
# Для Caddy/Funnel правильно 127.0.0.1:8080 (наружу проксирует :443).
```

### "Module not found"
```bash
# Переустановить зависимости
rm -rf venv
# Запустите соответствующий install-скрипт
./tools/install_ubuntu_local.sh
# или ./tools/install_ubuntu_tailscale_funnel.sh
# или ./tools/install_ubuntu_caddy.sh yourdomain.com
```

### Сервис стартует через ~2 минуты после загрузки

Если юнит ждёт `network-online.target`, а в `systemd-analyze blame` видна строка
`systemd-networkd-wait-online.service` с временем ~2 минуты — этот сервис висит до
таймаута на машинах, где сетью управляет NetworkManager (networkd не настроен).

```bash
# Диагностика: кто задерживает network-online.target
systemd-analyze blame | grep -i wait-online
systemd-analyze critical-chain tlibapp.service

# Решение: замаскировать лишний wait-online (disable недостаточно — его подтягивает systemd-networkd)
sudo systemctl mask systemd-networkd-wait-online.service
```

После перезагрузки `network-online.target` обеспечивается только `NetworkManager-wait-online` (секунды).

### Сервис не стартует: "Permission denied" на ExecStart

Копирование `.sh` через SFTP/WinSCP сбрасывает бит исполняемости. После каждого
копирования стартовых скриптов восстанавливайте права и проверяйте юнит:

```bash
chmod +x /opt/TlibWebApp/start_ubuntu*.sh
systemd-analyze verify /etc/systemd/system/tlibapp.service
```

---

**Следующий шаг:** [Руководство администратора](ADMIN.md)
