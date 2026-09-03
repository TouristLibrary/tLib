#!/bin/bash
# Version 3.3 - 02.09.2026 14:00:00 GMT
# Скрипт запуска FastAPI приложения TlibWebApp на Ubuntu Server с поддержкой Tailscale Funnel
# Изменения v3.1: настройка Tailscale Funnel сделана нефатальной (set +e + trap - ERR после обязательных
#                 проверок) — транзиентная ошибка "tailscale funnel reset"/"--bg" на старте больше не
#                 прерывает запуск uvicorn (ранее ERR-trap → handle_error → exit 1 роняли запуск).
# Изменения v3.2: "tailscale funnel reset" в cleanup() обёрнут в timeout 10 — зависший tailscaled
#                 при остановке больше не тянет graceful shutdown до systemd TimeoutStopSec и SIGKILL.
#                 Рекомендация для systemd-автозапуска изменилась: см. doc/DEPLOY.md — Funnel персистентен
#                 и настраивается один раз, сервис использует start_ubuntu_local.sh без управления funnel.
# Изменения v3.3: TS_HOSTNAME/TS_IP очищены перед публикацией репозитория (значения конкретного сервера
#                 не должны попадать в открытый код) — при пустых переменных скрипт определяет их
#                 автоматически из "tailscale status --json" (см. ниже, п.10).
# Описание: Запускает FastAPI сервер на порту 8080 и автоматически настраивает Tailscale Funnel для публичного доступа через интернет
# Функциональность:
# - Активирует виртуальную среду Python и запускает FastAPI сервер
# - Проверяет доступность и статус Tailscale
# - Настраивает Tailscale Funnel для публикации сайта в интернет через HTTPS
# - Ведет подробное логирование всех операций с Tailscale в logs/funnel.log
# - Обеспечивает корректное завершение работы с остановкой Funnel при выходе
# - Обработка сигналов для graceful shutdown сервера и Funnel

# Перед первым запуском выполнить: 
# sudo tailscale set --operator=$USER
# для использования скрипта в качестве оператора Tailscale

set -e  # Прервать выполнение при любой ошибке

# Настройка переменных
PORT=8080
HOST="0.0.0.0"  # Для доступа из локальной сети
APP_MODULE="app:app"

# Tailscale параметры (из вывода tailscale status)
# Оставьте пустыми для автоопределения при каждом запуске, либо укажите
# конкретные значения вашего сервера, чтобы пропустить автоопределение
TS_HOSTNAME=""
TS_IP=""

# Функция для логирования
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Функция для логирования Tailscale Funnel операций
log_funnel() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$msg"
    echo "$msg" >> logs/funnel.log 2>/dev/null || true
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
    
    # Остановка Tailscale Funnel
    log_funnel "=================================================="
    log_funnel "ОСТАНОВКА TAILSCALE FUNNEL"
    log_funnel "=================================================="
    
    if command -v tailscale >/dev/null 2>&1; then
        log_funnel "Отключение Tailscale Funnel..."
        log_funnel "Команда: tailscale funnel reset (timeout 10s)"
        timeout 10 tailscale funnel reset 2>&1 | tee -a logs/funnel.log || true
        log_funnel "✓ Tailscale Funnel остановлен"
    else
        log_funnel "Tailscale не найден, пропускаем остановку funnel"
    fi
    
    # Остановка FastAPI сервера
    log "Остановка FastAPI сервера..."
    if [ ! -z "$SERVER_PID" ]; then
        kill -TERM "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    log "Сервер остановлен"
    log_funnel "=================================================="
    log_funnel "ЗАВЕРШЕНИЕ РАБОТЫ ЗАВЕРШЕНО"
    log_funnel "=================================================="
    exit 0
}

# Установка обработчиков сигналов
trap 'handle_error $LINENO' ERR
trap cleanup SIGINT SIGTERM

log "=================================================="
log "Запуск TlibWebApp FastAPI сервера (Tailscale Funnel)"
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
    log "Запустите tools/install_ubuntu_tailscale_funnel.sh для установки зависимостей"
    exit 1
fi

if [ ! -f "venv/bin/activate" ]; then
    log "ОШИБКА: Файл активации виртуальной среды не найден!"
    log "Пересоздайте виртуальную среду с помощью tools/install_ubuntu_tailscale_funnel.sh"
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
    log "Запустите tools/install_ubuntu_tailscale_funnel.sh для установки зависимостей"
    exit 1
fi

log "✓ Основные зависимости найдены"

# Tailscale Funnel и запуск сервера — best-effort: транзиентные ошибки
# (например, "tailscale funnel reset" сразу после загрузки) не должны прерывать
# запуск uvicorn. Обязательные проверки выше уже делают явный exit 1.
# Нужны оба: set +e снимает авто-выход по errexit, а trap - ERR снимает обработчик
# handle_error (ERR-trap срабатывает независимо от set +e и иначе сделал бы exit 1).
set +e
trap - ERR

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
log "НАСТРОЙКА TAILSCALE FUNNEL"
log "=================================================="

# Инициализация лог-файла для funnel
log_funnel "=================================================="
log_funnel "НАЧАЛО НАСТРОЙКИ TAILSCALE FUNNEL"
log_funnel "Дата: $(date '+%Y-%m-%d %H:%M:%S %Z')"
log_funnel "Порт: $PORT"
log_funnel "=================================================="

# Проверка наличия Tailscale
log_funnel "8. Проверка установки Tailscale..."
if ! command -v tailscale >/dev/null 2>&1; then
    log_funnel "ОШИБКА: Tailscale не установлен!"
    log_funnel "Установите Tailscale командой:"
    log_funnel "  curl -fsSL https://tailscale.com/install.sh | sh"
    log_funnel "После установки выполните: tailscale up"
    log_funnel "ВНИМАНИЕ: Сайт будет доступен только локально, без публичного доступа через интернет"
    log "⚠ ВНИМАНИЕ: Tailscale не установлен - публичный доступ через интернет будет недоступен"
    log "   Сайт будет доступен только локально"
else
    log_funnel "✓ Tailscale установлен: $(tailscale version 2>/dev/null || echo 'версия неизвестна')"
    
    # Проверка статуса подключения Tailscale
    log_funnel "9. Проверка статуса подключения Tailscale..."
    TS_STATUS=$(tailscale status --json 2>&1)
    TS_EXIT_CODE=$?
    
    log_funnel "Команда: tailscale status --json"
    log_funnel "Exit code: $TS_EXIT_CODE"
    log_funnel "Вывод команды:"
    echo "$TS_STATUS" >> logs/funnel.log
    
    if [ $TS_EXIT_CODE -ne 0 ]; then
        log_funnel "ОШИБКА: Tailscale не подключен или не аутентифицирован!"
        log_funnel "Выполните команду для входа в Tailscale:"
        log_funnel "  sudo tailscale up"
        log_funnel "ВНИМАНИЕ: Сайт будет доступен только локально"
        log "⚠ ВНИМАНИЕ: Tailscale не подключен - публичный доступ будет недоступен"
    else
        # Получаем Tailscale hostname
        log_funnel "10. Использование Tailscale параметров..."
        
        # Проверяем заданы ли параметры в переменных скрипта
        if [ -z "$TS_HOSTNAME" ]; then
            log_funnel "Hostname не задан в переменных скрипта, попытка автоматического определения..."
            # Используем упрощенный grep - берем первое вхождение DNSName
            TS_HOSTNAME=$(echo "$TS_STATUS" | grep -m 1 -o '"DNSName":"[^"]*"' | cut -d'"' -f4 | sed 's/\.$//' 2>/dev/null || echo "")
            
            if [ -z "$TS_HOSTNAME" ]; then
                log_funnel "Попытка альтернативного метода через текстовый вывод..."
                TS_HOSTNAME=$(tailscale status 2>/dev/null | grep "^$(hostname)" | awk '{print $2}' 2>/dev/null || echo "")
            fi
            
            if [ ! -z "$TS_HOSTNAME" ]; then
                log_funnel "✓ Hostname определен автоматически: $TS_HOSTNAME"
            fi
        else
            log_funnel "✓ Используется hostname из переменных скрипта: $TS_HOSTNAME"
        fi
        
        if [ -z "$TS_IP" ]; then
            log_funnel "IP не задан в переменных, попытка автоматического определения..."
            # Ищем первый IPv4 адрес в массиве TailscaleIPs
            TS_IP=$(echo "$TS_STATUS" | grep -o '"TailscaleIPs":\[[^]]*\]' | head -1 | grep -o '[0-9]\{1,3\}\.[0-9]\{1,3\}\.[0-9]\{1,3\}\.[0-9]\{1,3\}' | head -1 2>/dev/null || echo "")
            
            if [ ! -z "$TS_IP" ]; then
                log_funnel "✓ IP определен автоматически: $TS_IP"
            fi
        else
            log_funnel "✓ Используется IP из переменных скрипта: $TS_IP"
        fi
        
        log_funnel "Итоговые параметры Tailscale:"
        log_funnel "  Hostname: $TS_HOSTNAME"
        log_funnel "  IP: $TS_IP"
        
        if [ -z "$TS_HOSTNAME" ]; then
            log_funnel "ОШИБКА: Не удалось определить Tailscale hostname"
            log_funnel "ВНИМАНИЕ: Funnel не может быть настроен без hostname"
            log "⚠ ВНИМАНИЕ: Не удалось определить Tailscale hostname"
        else
            # Очистка предыдущих настроек
            log_funnel "11. Очистка предыдущих настроек Funnel..."
            log_funnel "Команда: tailscale funnel reset"
            FUNNEL_RESET_OUTPUT=$(tailscale funnel reset 2>&1)
            log_funnel "Результат сброса:"
            echo "$FUNNEL_RESET_OUTPUT" >> logs/funnel.log
            log_funnel "✓ Предыдущие настройки очищены"
            
            # Включение Funnel (автоматически настраивает и serve, и funnel)
            log_funnel "12. Включение Tailscale Funnel для порта $PORT..."
            log_funnel "Команда: tailscale funnel --bg $PORT"
            log_funnel "Это настроит и serve, и funnel автоматически"
            log_funnel "Это может занять несколько секунд..."
            
            FUNNEL_ENABLE_OUTPUT=$(tailscale funnel --bg $PORT 2>&1)
            FUNNEL_ENABLE_EXIT=$?
            
            log_funnel "Exit code: $FUNNEL_ENABLE_EXIT"
            log_funnel "Вывод команды:"
            echo "$FUNNEL_ENABLE_OUTPUT" >> logs/funnel.log
            
            if [ $FUNNEL_ENABLE_EXIT -ne 0 ]; then
                log_funnel "ОШИБКА: Не удалось включить Funnel!"
                log_funnel "Возможные причины:"
                log_funnel "  1. Funnel не включен в настройках Tailscale аккаунта"
                log_funnel "  2. Недостаточно прав доступа (требуется sudo)"
                log_funnel "  3. Порт $PORT уже используется"
                log_funnel "Решение:"
                log_funnel "  - Проверьте настройки на https://login.tailscale.com/admin/settings/features"
                log_funnel "  - Убедитесь что Funnel включен для вашего аккаунта"
                log_funnel "  - Убедитесь что пользователь может использовать sudo"
                log "⚠ ВНИМАНИЕ: Не удалось включить Tailscale Funnel"
                log "   Проверьте logs/funnel.log для подробностей"
            else
                log_funnel "✓ Tailscale Funnel успешно включен!"
                
                # Проверка статуса Funnel
                log_funnel "13. Проверка статуса Funnel..."
                log_funnel "Команда: tailscale funnel status"
                FUNNEL_STATUS=$(tailscale funnel status 2>&1)
                log_funnel "Статус Funnel:"
                echo "$FUNNEL_STATUS" >> logs/funnel.log
                
                # Формирование публичного URL
                PUBLIC_URL="https://${TS_HOSTNAME}"
                log_funnel "✓ Funnel активен и работает!"
                log_funnel "=================================================="
                log_funnel "ПУБЛИЧНЫЙ ДОСТУП НАСТРОЕН"
                log_funnel "=================================================="
                log_funnel "Публичный URL: $PUBLIC_URL"
                log_funnel "Tailscale IP: $TS_IP"
                log_funnel "Локальный порт приложения: $PORT"
                log_funnel "Маршрутизация: Internet -> $PUBLIC_URL:443 -> Funnel -> localhost:$PORT"
                log_funnel "=================================================="
                
                log "✓ Tailscale Funnel активен!"
                log "   Публичный URL: $PUBLIC_URL"
            fi
        fi
    fi
fi

log "=================================================="
log "ЗАПУСК СЕРВЕРА"
log "=================================================="
log "FastAPI сервер запускается..."
log "Host: $HOST (доступ из локальной сети)"  
log "Port: $PORT"
log "URL для локального доступа: http://localhost:$PORT/"
log "URL для доступа из сети: http://$LOCAL_IP:$PORT/"

# Вывод информации о публичном доступе через Tailscale Funnel
if [ ! -z "$PUBLIC_URL" ]; then
    log ""
    log "🌐 ПУБЛИЧНЫЙ ДОСТУП ЧЕРЕЗ ИНТЕРНЕТ:"
    log "   URL: $PUBLIC_URL"
    log "   (Tailscale Funnel активен)"
fi

log ""
log "Для остановки сервера нажмите Ctrl+C"
log "=================================================="

# Установка переменной окружения для порта
export PORT=$PORT

# Запуск uvicorn сервера с отслеживанием изменений и быстрым завершением
python -m uvicorn "$APP_MODULE" \
    --host "$HOST" \
    --port "$PORT" \
    --log-level info \
    --access-log \
    --timeout-graceful-shutdown 3 &

# Сохранение PID процесса для graceful shutdown
SERVER_PID=$!

# Небольшая пауза для инициализации сервера
sleep 3

# Финальная проверка статуса Serve/Funnel после запуска сервера
if command -v tailscale >/dev/null 2>&1 && [ ! -z "$PUBLIC_URL" ]; then
    log_funnel "=================================================="
    log_funnel "ФИНАЛЬНАЯ ПРОВЕРКА ПОСЛЕ ЗАПУСКА СЕРВЕРА"
    log_funnel "=================================================="
    
    # Проверяем что сервер отвечает локально
    if command -v curl >/dev/null 2>&1; then
        log_funnel "Проверка локального доступа к серверу..."
        HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:$PORT/ 2>/dev/null || echo "000")
        if [ "$HTTP_CODE" = "200" ]; then
            log_funnel "✓ Сервер отвечает на http://localhost:$PORT/ (код: $HTTP_CODE)"
        else
            log_funnel "⚠ ВНИМАНИЕ: Сервер не отвечает на локальные запросы (код: $HTTP_CODE)"
            log_funnel "   Возможно сервер еще не полностью запустился"
        fi
    fi
    
    log_funnel "Команда: tailscale funnel status"
    FINAL_FUNNEL_STATUS=$(tailscale funnel status 2>&1)
    log_funnel "Финальный статус Funnel:"
    echo "$FINAL_FUNNEL_STATUS" >> logs/funnel.log
    
    log_funnel "=================================================="
    log_funnel "СИСТЕМА ГОТОВА К РАБОТЕ"
    log_funnel "=================================================="
    log_funnel "✓ FastAPI сервер запущен на localhost:$PORT"
    log_funnel "✓ Tailscale Funnel обеспечивает публичный доступ"
    log_funnel "Публичный URL: $PUBLIC_URL"
    log_funnel "Логи funnel сохраняются в logs/funnel.log"
    log_funnel "=================================================="
fi

# Ожидание завершения сервера
wait "$SERVER_PID"
