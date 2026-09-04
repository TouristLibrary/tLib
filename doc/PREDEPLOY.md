# Подготовка сервера

Пошаговая настройка сервера **перед** развёртыванием приложения ([DEPLOY.md](DEPLOY.md)).

Документ состоит из двух частей:
- **Часть A** — минимальная подготовка (ВМ + ОС + SSH), достаточная для перехода к DEPLOY.md
- **Часть B** — усиление безопасности и настройка admin-доступа (рекомендуется для production)

---

## Часть A. Минимальная подготовка

### A1. Создание виртуальной машины

| Параметр | Рекомендация |
|----------|-------------|
| **CPU** | 4 vCPU |
| **RAM** | 8 ГБ |
| **Диск** | SSD, 1 ТБ |
| **Сеть** | 100 Мбит/с+ |

> **Почему SSD:** Приложение интенсивно работает с файлами — случайное чтение из ZIP-архивов, PNG-кеш, SQLite. На HDD будут ощутимые задержки при просмотре отчётов.
>
> **Разделение хранилища:** Можно разделить диск на два: ~20% — быстрое SSD-хранилище для кеша (`data.cache/`, `data.db/`) и ~80% — медленное хранилище для архивов (`data/`). Архивы можно разместить на отдельном сервере (NFS, SMB).
>
> **Почему 4 vCPU:** Конвертация PDF→PNG использует до 4 параллельных потоков. При активном просмотре PDF несколькими пользователями 2 ядра создадут заметные задержки.
>
> **Почему 1 ТБ:** Текущий объём архивов ~566 ГБ + файловый кеш до 150 ГБ + рост ~27 ГБ/год.

> **Фиксация статического IP:** После установки ОС VM может получать разные IP при перезагрузке. Чтобы зафиксировать текущий IP — см. [tools/VM-Static-IP.md](../tools/VM-Static-IP.md).

### A2. Установка Ubuntu Server 24.04 LTS

1. Скачайте ISO: [ubuntu.com/download/server](https://ubuntu.com/download/server)
2. При установке выберите **Ubuntu Server (minimized)** — без GUI
3. Настройте:
   - Имя хоста: `tlib` (или по вашему выбору)
   - Пользователь: `tlib-admin`
   - Пароль: сложный (позже отключим вход по паролю)
   - Диск: использовать весь диск (LVM по желанию)
   - **Включите OpenSSH Server** в процессе установки

4. После установки обновите систему:
```bash
sudo apt update && sudo apt upgrade -y && sudo reboot
```

### A3. Проверка SSH-доступа

С рабочей машины подключитесь к серверу:

```bash
ssh tlib-admin@IP-СЕРВЕРА
```

Если подключение успешно — сервер готов к [развёртыванию приложения](DEPLOY.md).

---

## Часть B. Безопасность и admin-доступ

### B1. SSH-ключи (отключение паролей)

**На рабочей машине** (Windows PowerShell, macOS/Linux Terminal):

```bash
# Генерация ключа (если ещё нет)
ssh-keygen -t ed25519 -C "tlib-admin"

# Копирование на сервер
ssh-copy-id -i ~/.ssh/id_ed25519.pub tlib-admin@IP-СЕРВЕРА
```

**Windows (если нет ssh-copy-id):**

```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh tlib-admin@IP-СЕРВЕРА "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

Проверьте вход по ключу:

```bash
ssh tlib-admin@IP-СЕРВЕРА
# Должен пустить без запроса пароля
```

**На сервере** — отключите вход по паролю:

```bash
printf 'PasswordAuthentication no\nPermitRootLogin no\n' | sudo tee /etc/ssh/sshd_config.d/99-hardening.conf
sudo systemctl restart ssh
```

> Файл в `sshd_config.d/` перекрывает настройки из основного `sshd_config` и из `50-cloud-init.conf` (если есть). Это гарантирует отключение паролей независимо от способа установки ОС.

> **Важно:** Не закрывайте текущую SSH-сессию. Откройте **вторую** сессию и убедитесь, что вход по ключу работает. Если заблокируете себя — понадобится доступ через консоль гипервизора.

### B2. Firewall (ufw)

```bash
sudo apt install -y ufw

sudo ufw default deny incoming
sudo ufw default allow outgoing

# SSH — по умолчанию с любого IP (ограничим после настройки Tailscale)
sudo ufw allow 22/tcp

# HTTPS для Caddy
sudo ufw allow 443/tcp

# HTTP для редиректа и ACME challenge (Let's Encrypt)
sudo ufw allow 80/tcp

sudo ufw enable
```

> **Порт 8080 НЕ открываем наружу** — его проксирует Caddy. Приложение слушает только localhost:8080.

Проверка:

```bash
sudo ufw status
```

Ожидаемый вывод (до настройки B3, после — правило для 22/tcp изменится):
```
To                         Action      From
--                         ------      ----
22/tcp                     ALLOW       Anywhere
443/tcp                    ALLOW       Anywhere
80/tcp                     ALLOW       Anywhere
```

### B3. Ограничение SSH-доступа (Tailscale + доверенные IP)

Tailscale создаёт зашифрованный VPN-туннель для admin-доступа. В дополнение можно разрешить SSH с доверенных IP-адресов и подсетей. После настройки SSH-порт будет закрыт из публичного интернета.

**Установка:**

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

В терминале появится ссылка для авторизации — скопируйте её и откройте в браузере на рабочей машине. После авторизации:

```bash
# Запомните Tailscale IP (100.x.x.x)
tailscale ip -4
```

**Установите Tailscale на рабочую машину** — [tailscale.com/download](https://tailscale.com/download).

Проверьте подключение через Tailscale:

```bash
ssh tlib-admin@100.x.x.x
```

**Ограничьте SSH — Tailscale + доверенные IP/подсети:**

```bash
sudo ufw delete allow 22/tcp

# Tailscale VPN
sudo ufw allow from 100.64.0.0/10 to any port 22

# Доверенные IP и подсети (замените на свои)
sudo ufw allow from 203.0.113.10 to any port 22     # Статический IP (пример)
sudo ufw allow from 192.168.1.0/24 to any port 22   # Локальная подсеть (пример)

sudo ufw reload
```

> Удалите или добавьте строки `sudo ufw allow from ... to any port 22` под ваши реальные IP-адреса и подсети. Не оставляйте примеры `203.0.113.10` и `192.168.1.0/24` без замены.

Теперь SSH-порт закрыт из публичного интернета — доступ только через Tailscale VPN и с доверенных адресов.

### B4. Автообновления безопасности

Ubuntu 24.04 включает `unattended-upgrades` по умолчанию. Проверьте что он активен:

```bash
sudo systemctl status unattended-upgrades
```

Если не активен:

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

### B5. Настройка DNS (для Caddy)

Для работы Caddy с Let's Encrypt нужна DNS-запись, указывающая на сервер.

В панели управления DNS вашего домена создайте записи:

| Тип | Имя | Значение | TTL |
|-----|-----|----------|-----|
| A | `tlib` (или `@`) | `ПУБЛИЧНЫЙ-IP-СЕРВЕРА` | 300 |
| A | `www` | `ПУБЛИЧНЫЙ-IP-СЕРВЕРА` | 300 |

> Запись для `www` нужна, чтобы Caddy получил сертификат и настроил редирект `www.домен` → `домен`.

> Публичный IP сервера можно узнать командой: `curl -4 ifconfig.me`

Дождитесь обновления DNS (обычно 5–15 минут):

```bash
dig +short tlib.yourdomain.com
# Должен вернуть IP сервера
```

Подробнее: [Инструкция по настройке DNS](details/DNS_SETUP.md).

### B6. Бэкап данных (cron + rsync)

Добавьте ежедневный бэкап на внешнее хранилище:

```bash
crontab -e
```

```cron
# Ежедневно в 03:00 — бэкап БД и архивов
0 3 * * * rsync -a /opt/TlibWebApp/data.db/tlib.db /backup/tlib/tlib.db
0 3 * * * rsync -a --delete /opt/TlibWebApp/data/ /backup/tlib/data/
```

> Замените `/backup/tlib/` на путь к вашему хранилищу (внешний диск, NFS, второй сервер через SSH).

### B7. Мониторинг (UptimeRobot)

Приложение предоставляет endpoint `/health` для проверки состояния.

1. Зарегистрируйтесь на [uptimerobot.com](https://uptimerobot.com) (бесплатно до 50 мониторов)
2. Добавьте HTTP(s) Monitor:
   - URL: `https://yourdomain.com/health`
   - Interval: 5 минут
3. Настройте уведомления (email/Telegram)

---

## Итоговая схема доступа

```
┌──────────────────────────────────────────────────────────┐
│                      ИНТЕРНЕТ                            │
│                                                          │
│  Пользователи ──► :443 (Caddy) ──► :8080 (TlibWebApp)  │
│                   HTTPS, Let's Encrypt                   │
│                                                          │
│  Админ ──► Tailscale VPN ──────► :22 (SSH/SFTP)          │
│         ──► Доверенный IP/подсеть ─►                     │
│            порт 22 закрыт из публичного интернета        │
└──────────────────────────────────────────────────────────┘
```

---

## Чеклист

| # | Шаг | Часть | Статус |
|---|-----|-------|--------|
| 1 | Создать ВМ (4 vCPU, 4 ГБ RAM, SSD 1 ТБ) | A | ☐ |
| 2 | Установить Ubuntu Server 24.04 LTS | A | ☐ |
| 3 | Обновить систему, проверить SSH | A | ☐ |
| 4 | Настроить SSH-ключи, отключить пароли | B | ☐ |
| 5 | Настроить firewall (ufw) | B | ☐ |
| 6 | Установить Tailscale, ограничить SSH | B | ☐ |
| 7 | Проверить автообновления | B | ☐ |
| 8 | Настроить DNS для домена | B | ☐ |
| 9 | Настроить бэкап | B | ☐ |
| 10 | Подключить мониторинг | B | ☐ |

---

**Следующий шаг:** [Развёртывание приложения](DEPLOY.md)
