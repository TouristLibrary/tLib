# Version 1.0 - 08.01.2026 20:19:47 GMT
# Security helpers package for TlibWebApp
# Описание: Пакет содержит функции безопасности уровня services (валидация путей, защита от Path Traversal),
#           используемые роутерами и сервисами как единый "источник правды" для filesystem-checks.
# Модули:
# - path_validation.py: валидация и декодирование путей, защита от Path Traversal атак
