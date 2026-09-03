#!/bin/bash
# Version 3.0 - 19.01.2026
# Wrapper-скрипт для запуска TlibWebApp
# По умолчанию вызывает start_ubuntu_tailscale_funnel.sh
#
# Доступные режимы запуска:
#   ./start_ubuntu_local.sh             - Локальный сервер (LAN, без HTTPS)
#   ./start_ubuntu_tailscale_funnel.sh  - Tailscale Funnel (публичный HTTPS)
#   ./start_ubuntu_caddy.sh             - Caddy reverse proxy (Let's Encrypt HTTPS)
#   ./start_ubuntu.sh                   - Этот wrapper (по умолчанию Tailscale Funnel)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/start_ubuntu_tailscale_funnel.sh" "$@"
