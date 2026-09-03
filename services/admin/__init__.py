# Version 1.0 - 15.06.2026 17:05:00 GMT
# Admin services package для TlibWebApp
# Описание: Сервисы операционной аналитики и мониторинга для панели администратора.
# - status_service.py: сбор данных о здоровье системы, дисках, росте, трафике и безопасности.

from .status_service import collect_health, collect_status

__all__ = [
    'collect_health',
    'collect_status',
]
