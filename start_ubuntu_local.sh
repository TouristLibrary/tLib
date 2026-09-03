#!/bin/bash
# Version 2.0 - 19.01.2026
# Скрипт запуска FastAPI приложения TlibWebApp (режим: локальный сервер)
# Активирует виртуальную среду и запускает FastAPI сервер на порту 8080 для доступа из локальной сети
# Обработка сигналов для корректного завершения работы

set -e  # Прервать выполнение при любой ошибке

# Настройка переменных
PORT=8080
HOST="0.0.0.0"  # Для доступа из локальной сети
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
    exit 0
}

# Установка обработчиков сигналов
trap 'handle_error $LINENO' ERR
trap cleanup SIGINT SIGTERM

log "=================================================="
log "Запуск TlibWebApp FastAPI сервера (локальный режим)"
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
    log "Запустите tools/install_ubuntu_local.sh для установки зависимостей"
    exit 1
fi

if [ ! -f "venv/bin/activate" ]; then
    log "ОШИБКА: Файл активации виртуальной среды не найден!"
    log "Пересоздайте виртуальную среду с помощью tools/install_ubuntu_local.sh"
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
    log "Запустите tools/install_ubuntu_local.sh для установки зависимостей"
    exit 1
fi

log "✓ Основные зависимости найдены"

# Получение информации о сервере
LOCAL_IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "неизвестен")
HOSTNAME=$(hostname 2>/dev/null || echo "неизвестен")

log "5. Информация о сервере:"
log "   Hostname: $HOSTNAME"
log "   Local IP: $LOCAL_IP"
log "   Port: $PORT"

# Проверка доступности порта
log "6. Проверка доступности порта $PORT..."
if command -v netstat >/dev/null 2>&1; then
    if netstat -tuln | grep ":$PORT " >/dev/null 2>&1; then
        log "ВНИМАНИЕ: Порт $PORT уже используется другим процессом"
        log "Остановите другой процесс или измените порт в скрипте"
        # Не завершаем работу, uvicorn сам выдаст ошибку если порт занят
    fi
fi

log "7. Создание директорий (если не существуют)..."
mkdir -p data css js assets logs

log "=================================================="
log "ЗАПУСК СЕРВЕРА (ЛОКАЛЬНЫЙ РЕЖИМ)"
log "=================================================="
log "FastAPI сервер запускается..."
log "Режим: Локальный сервер (LAN, без HTTPS)"
log "Host: $HOST (доступ из локальной сети)"  
log "Port: $PORT"
log "URL для локального доступа: http://localhost:$PORT/"
log "URL для доступа из сети: http://$LOCAL_IP:$PORT/"
log ""
log "Для остановки сервера нажмите Ctrl+C"
log "=================================================="

# Установка переменной окружения для порта
export PORT=$PORT

# Запуск uvicorn сервера
python -m uvicorn "$APP_MODULE" \
    --host "$HOST" \
    --port "$PORT" \
    --log-level info \
    --access-log &

# Сохранение PID процесса для graceful shutdown
SERVER_PID=$!

# Ожидание завершения сервера
wait "$SERVER_PID"
