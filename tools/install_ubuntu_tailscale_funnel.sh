#!/bin/bash
# Version 1.0 - 19.01.2026
# Скрипт установки TlibWebApp с Tailscale Funnel
# Устанавливает зависимости + Tailscale для публичного HTTPS доступа

set -e  # Прервать выполнение при любой ошибке

# Импорт общих функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/install_ubuntu_common.sh"

# Установка обработчика ошибок
trap 'handle_error $LINENO' ERR

echo "=================================================="
echo "Установка TlibWebApp (режим: Tailscale Funnel)"
echo "=================================================="

log "1. Обновление системных пакетов..."
sudo apt update
sudo apt upgrade -y

log "2. Проверка и установка Python..."
check_and_install_python

log "3. Создание виртуальной среды..."
create_venv

log "4. Установка зависимостей..."
install_requirements

log "5. Создание директорий..."
create_directories

log "6. Проверка установки Tailscale..."
if ! command -v tailscale >/dev/null 2>&1; then
    log "Tailscale не установлен. Установка..."
    curl -fsSL https://tailscale.com/install.sh | sh
    log "✓ Tailscale установлен"
else
    log "✓ Tailscale уже установлен: $(tailscale version 2>/dev/null || echo 'версия неизвестна')"
fi

log "7. Проверка статуса Tailscale..."
if ! tailscale status >/dev/null 2>&1; then
    log ""
    log "⚠ ВНИМАНИЕ: Tailscale не подключен!"
    log ""
    log "Для завершения настройки выполните:"
    log "  1. sudo tailscale up"
    log "  2. sudo tailscale set --operator=\$USER"
    log ""
    log "После этого можно запускать приложение."
else
    TS_HOSTNAME=$(tailscale status --json 2>/dev/null | grep -m 1 -o '"DNSName":"[^"]*"' | cut -d'"' -f4 | sed 's/\.$//' || echo "")
    if [ ! -z "$TS_HOSTNAME" ]; then
        log "✓ Tailscale подключен"
        log "  Hostname: $TS_HOSTNAME"
        log ""
        log "Убедитесь что вы установили operator права:"
        log "  sudo tailscale set --operator=\$USER"
    fi
fi

LOCAL_IP=$(hostname -I | awk '{print $1}')
log "Локальный IP адрес сервера: $LOCAL_IP"

log "=================================================="
log "✓ УСТАНОВКА ЗАВЕРШЕНА УСПЕШНО!"
log "=================================================="
log ""
log "Режим: Tailscale Funnel (публичный HTTPS)"
log "Приложение будет доступно через интернет с автоматическим HTTPS"
log ""
log "Для запуска приложения выполните:"
log "  ./start_ubuntu_tailscale_funnel.sh"
log ""
log "или используйте обычный wrapper:"
log "  ./start_ubuntu.sh"
log ""
log "Для остановки приложения нажмите Ctrl+C"
log "=================================================="

# Создание файла с информацией о развертывании
print_deployment_info "./start_ubuntu_tailscale_funnel.sh" "Tailscale Funnel (публичный HTTPS)"

log "Информация о развертывании сохранена в deployment_info.txt"
