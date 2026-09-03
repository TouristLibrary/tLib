#!/bin/bash
# Version 1.0 - 19.01.2026
# Общие функции для скриптов установки TlibWebApp на Ubuntu Server 24.04
# Этот файл импортируется (source) в install_ubuntu_*.sh скрипты

# Функция для логирования
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# Функция обработки ошибок
handle_error() {
    echo "ОШИБКА: Произошла ошибка на строке $1"
    echo "Проверьте вывод команд выше для диагностики проблемы."
    exit 1
}

# Проверка и установка Python 3.10+
check_and_install_python() {
    log "Установка Python 3.10+ и необходимых пакетов..."
    sudo apt install -y python3 python3-pip python3-venv python3-dev build-essential
    
    # Проверка версии Python
    PYTHON_VERSION=$(python3 --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
    log "Установлена версия Python: $PYTHON_VERSION"
    
    # Проверка минимальной версии Python (3.10)
    if ! python3 -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
        log "ВНИМАНИЕ: Требуется Python 3.10+. Попытка установки Python 3.11..."
        sudo apt install -y software-properties-common
        sudo add-apt-repository ppa:deadsnakes/ppa -y
        sudo apt update
        sudo apt install -y python3.11 python3.11-venv python3.11-dev
        
        # Создание симлинка для python3
        sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1
    fi
}

# Создание виртуальной среды
create_venv() {
    log "Создание виртуальной среды..."
    
    # Переход в корневую директорию проекта
    cd "$(dirname "$0")/.."
    
    if [ -d "venv" ]; then
        log "Виртуальная среда уже существует, пересоздаем..."
        rm -rf venv
    fi
    
    python3 -m venv venv
    log "Виртуальная среда создана в директории ./venv"
}

# Установка зависимостей из requirements.txt
install_requirements() {
    log "Активация виртуальной среды и установка зависимостей..."
    
    # Переход в корневую директорию проекта
    cd "$(dirname "$0")/.."
    
    source venv/bin/activate
    
    # Обновление pip в виртуальной среде
    pip install --upgrade pip
    
    # Установка зависимостей из requirements.txt
    if [ -f "requirements.txt" ]; then
        log "Установка зависимостей из requirements.txt..."
        pip install -r requirements.txt
        log "✓ Все зависимости установлены успешно"
    else
        log "ОШИБКА: Файл requirements.txt не найден!"
        log "Убедитесь что файл requirements.txt находится в текущей директории"
        exit 1
    fi
    
    log "Проверка установленных пакетов..."
    pip list | grep -E "(fastapi|uvicorn|starlette)"
}

# Создание файла с информацией о развертывании
print_deployment_info() {
    local START_SCRIPT="$1"
    local DEPLOY_TYPE="$2"
    
    # Переход в корневую директорию проекта
    cd "$(dirname "$0")/.."
    
    LOCAL_IP=$(hostname -I | awk '{print $1}')
    
    cat > deployment_info.txt << EOF
TlibWebApp - Информация о развертывании
=======================================
Дата установки: $(date)
Тип развертывания: ${DEPLOY_TYPE}
IP адрес сервера: $LOCAL_IP
Порт приложения: 8080

Директории проекта:
- Основной код: $(pwd)
- Виртуальная среда: $(pwd)/venv
- Статические файлы: $(pwd)/js, $(pwd)/css, $(pwd)/assets
- Архивы данных: $(pwd)/data

Команды управления:
- Запуск: ${START_SCRIPT}
- Остановка: Ctrl+C
- Просмотр логов: tail -f logs/app.log

Установленные пакеты:
$(pip list)
EOF
    
    log "Информация о развертывании сохранена в deployment_info.txt"
}

# Создание директорий если их нет
create_directories() {
    # Переход в корневую директорию проекта
    cd "$(dirname "$0")/.."
    
    mkdir -p data css js assets logs
}
