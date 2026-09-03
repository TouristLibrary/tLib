#!/bin/bash
# Version 1.0 - 19.01.2026
# Скрипт установки TlibWebApp с Caddy (Let's Encrypt HTTPS)
# Использование: ./install_ubuntu_caddy.sh example.com [email@example.com]

set -e  # Прервать выполнение при любой ошибке

# Импорт общих функций
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/install_ubuntu_common.sh"

# Установка обработчика ошибок
trap 'handle_error $LINENO' ERR

# Проверка аргументов
if [ -z "$1" ]; then
    echo "ОШИБКА: Не указан домен!"
    echo ""
    echo "Использование:"
    echo "  $0 example.com [email@example.com]"
    echo ""
    echo "Пример:"
    echo "  $0 tlib.example.com admin@example.com"
    echo ""
    exit 1
fi

DOMAIN="$1"
EMAIL="${2:-}"

echo "=================================================="
echo "Установка TlibWebApp (режим: Caddy + Let's Encrypt)"
echo "=================================================="
log "Домен: $DOMAIN"
if [ ! -z "$EMAIL" ]; then
    log "Email: $EMAIL"
fi

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

log "6. Установка Caddy..."
if ! command -v caddy >/dev/null 2>&1; then
    log "Caddy не установлен. Установка..."
    sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
    sudo apt update
    sudo apt install -y caddy
    log "✓ Caddy установлен"
else
    log "✓ Caddy уже установлен: $(caddy version 2>/dev/null | head -1 || echo 'версия неизвестна')"
fi

log "7. Настройка Caddyfile..."
sudo tee /etc/caddy/Caddyfile > /dev/null <<EOF
# Caddyfile для TlibWebApp
# Автоматически получает и обновляет Let's Encrypt сертификаты

www.${DOMAIN} {
    redir https://${DOMAIN}{uri} permanent
}

${DOMAIN} {
    # Reverse proxy на локальный uvicorn сервер
    reverse_proxy 127.0.0.1:8080
    
    # Логирование
    log {
        output file /var/log/caddy/tlib.log
    }
    
    # Заголовки безопасности (дополнительно к FastAPI)
    header {
        # Скрыть версию сервера
        -Server
        
        # HSTS - принудительный HTTPS
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
    }
EOF

if [ ! -z "$EMAIL" ]; then
    sudo tee -a /etc/caddy/Caddyfile > /dev/null <<EOF
    
    # Email для уведомлений Let's Encrypt
    tls ${EMAIL}
EOF
fi

sudo tee -a /etc/caddy/Caddyfile > /dev/null <<EOF
}
EOF

log "✓ Caddyfile создан: /etc/caddy/Caddyfile"

log "8. Настройка firewall (UFW)..."
if command -v ufw >/dev/null 2>&1; then
    if sudo ufw status | grep -q "Status: active"; then
        log "Открытие портов 80 и 443..."
        sudo ufw allow 80/tcp
        sudo ufw allow 443/tcp
        log "✓ Порты 80 и 443 открыты в UFW"
    else
        log "UFW не активен, пропускаем настройку firewall"
    fi
else
    log "UFW не установлен, пропускаем настройку firewall"
fi

log "9. Перезапуск Caddy..."
sudo systemctl enable caddy
sudo systemctl restart caddy

# Проверка статуса
if sudo systemctl is-active --quiet caddy; then
    log "✓ Caddy запущен и работает"
else
    log "⚠ ВНИМАНИЕ: Caddy не запустился, проверьте логи:"
    log "  sudo journalctl -u caddy -n 50"
fi

LOCAL_IP=$(hostname -I | awk '{print $1}')

log "=================================================="
log "✓ УСТАНОВКА ЗАВЕРШЕНА УСПЕШНО!"
log "=================================================="
log ""
log "Режим: Caddy + Let's Encrypt (HTTPS)"
log "Приложение будет доступно через ваш домен с автоматическим HTTPS"
log ""
log "Настройка:"
log "  Домен: $DOMAIN"
log "  Локальный IP: $LOCAL_IP"
log "  Caddy слушает: 80, 443"
log "  FastAPI будет слушать: 127.0.0.1:8080 (только localhost)"
log ""
log "ВАЖНО: Убедитесь что DNS запись для домена $DOMAIN"
log "       указывает на IP адрес этого сервера!"
log ""
log "Для запуска приложения выполните:"
log "  ./start_ubuntu_caddy.sh"
log ""
log "Приложение будет доступно по адресу:"
log "  https://$DOMAIN/"
log ""
log "Проверка статуса Caddy:"
log "  sudo systemctl status caddy"
log "  sudo journalctl -u caddy -f"
log ""
log "Для остановки приложения нажмите Ctrl+C"
log "=================================================="

# Создание файла с информацией о развертывании
cd "$(dirname "$0")/.."
cat > deployment_info.txt << EOF
TlibWebApp - Информация о развертывании
=======================================
Дата установки: $(date)
Тип развертывания: Caddy + Let's Encrypt (HTTPS)
Домен: $DOMAIN
IP адрес сервера: $LOCAL_IP
URL доступа: https://$DOMAIN/

Директории проекта:
- Основной код: $(pwd)
- Виртуальная среда: $(pwd)/venv
- Статические файлы: $(pwd)/js, $(pwd)/css, $(pwd)/assets
- Архивы данных: $(pwd)/data
- Caddyfile: /etc/caddy/Caddyfile
- Логи Caddy: /var/log/caddy/

Команды управления:
- Запуск приложения: ./start_ubuntu_caddy.sh
- Остановка: Ctrl+C
- Статус Caddy: sudo systemctl status caddy
- Перезапуск Caddy: sudo systemctl restart caddy
- Логи Caddy: sudo journalctl -u caddy -f
- Логи приложения: tail -f logs/app.log

Установленные пакеты:
$(source venv/bin/activate && pip list)
EOF

log "Информация о развертывании сохранена в deployment_info.txt"
