#!/bin/bash
# Version 1.0 - 19.01.2026
# Скрипт запуска FastAPI приложения TlibWebApp с Caddy reverse proxy
# Запускает FastAPI на localhost:8080, проверяет статус Caddy
# Обработка сигналов для корректного завершения работы

set -e  # Прервать выполнение при любой ошибке

# Настройка переменных
PORT=8080
HOST="127.0.0.1"  # Только localhost (Caddy проксирует)
APP_MODULE="app:app"

# Функция для логирования
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Функция обработки ошибок
handle_error() {
    log "ОШИБКА: Произошла ошибка на строке $1"
    log "Проверьте что все файлы на месте и зависимости установлены"
    exit 1
}

# Функция для graceful shutdown
cleanup() {
    log "Получен сигнал завершения работы..."
    log "Остановка FastAPI сервера..."
    if [ ! -z "$SERVER_PID" ]; then
        kill -TERM "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    log "Сервер остановлен"
    log "Caddy остается запущенным (управляется systemd)"
    exit 0
}

# Установка обработчиков сигналов
trap 'handle_error $LINENO' ERR
trap cleanup SIGINT SIGTERM

log "=================================================="
log "Запуск TlibWebApp FastAPI сервера (Caddy режим)"
log "=================================================="

# Проверка существования основных файлов
log "1. Проверка файлов проекта..."
required_files=("app.py" "requirements.txt" "index.html")
missing_files=()

for file in "${required_files[@]}"; do
    if [ ! -f "$file" ]; then
        missing_files+=("$file")
    fi
done

if [ ${#missing_files[@]} -gt 0 ]; then
    log "ОШИБКА: Отсутствуют обязательные файлы:"
    for file in "${missing_files[@]}"; do
        log "  - $file"
    done
    log "Убедитесь что все файлы проекта скопированы правильно"
    exit 1
fi

log "✓ Все основные файлы найдены"

# Проверка виртуальной среды
log "2. Проверка виртуальной среды..."
if [ ! -d "venv" ]; then
    log "ОШИБКА: Виртуальная среда не найдена!"
    log "Запустите tools/install_ubuntu_caddy.sh для установки зависимостей"
    exit 1
fi

if [ ! -f "venv/bin/activate" ]; then
    log "ОШИБКА: Файл активации виртуальной среды не найден!"
    log "Пересоздайте виртуальную среду с помощью tools/install_ubuntu_caddy.sh"
    exit 1
fi

log "✓ Виртуальная среда найдена"

# Активация виртуальной среды
log "3. Активация виртуальной среды..."
source venv/bin/activate
log "✓ Виртуальная среда активирована"

# Проверка установленных зависимостей
log "4. Проверка зависимостей..."
if ! python -c "import fastapi, uvicorn" 2>/dev/null; then
    log "ОШИБКА: FastAPI или uvicorn не установлены!"
    log "Запустите tools/install_ubuntu_caddy.sh для установки зависимостей"
    exit 1
fi

log "✓ Основные зависимости найдены"

# Проверка установки и статуса Caddy
log "5. Проверка Caddy..."
if ! command -v caddy >/dev/null 2>&1; then
    log "ОШИБКА: Caddy не установлен!"
    log "Запустите tools/install_ubuntu_caddy.sh для установки Caddy"
    exit 1
fi

if systemctl is-active --quiet caddy; then
    log "✓ Caddy запущен"
else
    log "ВНИМАНИЕ: Caddy не запущен!"
    log "Запустите вручную: sudo systemctl start caddy"
fi

# Получение информации о сервере
LOCAL_IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "неизвестен")
HOSTNAME=$(hostname 2>/dev/null || echo "неизвестен")

# Попытка получить домен из Caddyfile
DOMAIN=$(grep -E "^[a-zA-Z0-9.-]+ {" /etc/caddy/Caddyfile 2>/dev/null | head -1 | awk '{print $1}' || echo "неизвестен")

log "6. Информация о сервере:"
log "   Hostname: $HOSTNAME"
log "   Local IP: $LOCAL_IP"
log "   Домен (из Caddyfile): $DOMAIN"
log "   FastAPI Port: $PORT (localhost only)"

# Проверка доступности порта
log "7. Проверка доступности порта $PORT..."
if command -v netstat >/dev/null 2>&1; then
    if netstat -tuln | grep "127.0.0.1:$PORT " >/dev/null 2>&1; then
        log "ВНИМАНИЕ: Порт $PORT уже используется другим процессом"
        log "Остановите другой процесс или измените порт в скрипте"
        # Не завершаем работу, uvicorn сам выдаст ошибку если порт занят
    fi
fi

log "8. Создание директорий (если не существуют)..."
mkdir -p data css js assets logs

log "=================================================="
log "ЗАПУСК СЕРВЕРА (CADDY РЕЖИМ)"
log "=================================================="
log "FastAPI сервер запускается..."
log "Режим: Caddy reverse proxy (HTTPS через Let's Encrypt)"
log "Host: $HOST (только localhost, Caddy проксирует)"  
log "Port: $PORT"
log "URL для локального доступа: http://localhost:$PORT/"
log ""
log "Публичный доступ через Caddy:"
if [ "$DOMAIN" != "неизвестен" ]; then
    log "  HTTPS URL: https://$DOMAIN/"
    log "  Caddy автоматически получает Let's Encrypt сертификаты"
else
    log "  Проверьте /etc/caddy/Caddyfile для настроенного домена"
fi
log ""
log "Управление Caddy:"
log "  Статус:      sudo systemctl status caddy"
log "  Перезапуск:  sudo systemctl restart caddy"
log "  Логи:        sudo journalctl -u caddy -f"
log ""
log "Для остановки сервера нажмите Ctrl+C"
log "=================================================="

# Установка переменной окружения для порта
export PORT=$PORT

# Запуск uvicorn сервера (только на localhost)
python -m uvicorn "$APP_MODULE" \
    --host "$HOST" \
    --port "$PORT" \
    --log-level info \
    --access-log \
    --proxy-headers \
    --forwarded-allow-ips="127.0.0.1" &

# Сохранение PID процесса для graceful shutdown
SERVER_PID=$!

# Небольшая пауза для инициализации сервера
sleep 2

# Проверка что сервер запустился
if command -v curl >/dev/null 2>&1; then
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:$PORT/ 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
        log "✓ Сервер отвечает на http://localhost:$PORT/ (код: $HTTP_CODE)"
        log "✓ Система готова к работе"
    else
        log "⚠ ВНИМАНИЕ: Сервер не отвечает (код: $HTTP_CODE)"
        log "   Возможно сервер еще не полностью запустился"
    fi
fi

# Ожидание завершения сервера
wait "$SERVER_PID"
