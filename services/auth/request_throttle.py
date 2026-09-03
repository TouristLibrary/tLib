# Version 1.0 - 21.06.2026 09:00:00 GMT
# Пер-IP троттлинг для POST /api/auth/request-link
# Описание: In-memory ограничение числа запросов request-link с одного IP-адреса.
#           Словарь ip -> (window_start, count) с периодической очисткой по образцу rate_limit.py.
#           allow_request_link(ip) -> (allowed: bool, retry_after: int) — основной публичный API.
#           Не зависит от SQLite; сбрасывается при рестарте процесса.
#           Параметры читаются из config: AUTH_REQUEST_LINK_IP_MAX, AUTH_REQUEST_LINK_IP_WINDOW.

import time
import threading
from typing import Dict, Tuple

from config import AUTH_REQUEST_LINK_IP_MAX, AUTH_REQUEST_LINK_IP_WINDOW

# ip -> (window_start: float, count: int)
_requests: Dict[str, Tuple[float, int]] = {}
_lock = threading.Lock()

# Очистка старых записей раз в 5 окон
_CLEANUP_INTERVAL = AUTH_REQUEST_LINK_IP_WINDOW * 5
_last_cleanup: float = time.time()


def _cleanup(now: float) -> None:
    """Удаляет устаревшие записи (IP, у которых окно уже истекло)."""
    to_delete = [
        ip for ip, (ts, _) in _requests.items()
        if now - ts > AUTH_REQUEST_LINK_IP_WINDOW
    ]
    for ip in to_delete:
        del _requests[ip]


def allow_request_link(ip: str) -> tuple[bool, int]:
    """Проверяет, разрешён ли ещё один запрос request-link с данного IP.

    Возвращает (allowed, retry_after):
    - allowed: True если запрос разрешён, False если лимит исчерпан.
    - retry_after: число секунд до сброса окна (0 если разрешён).
    """
    global _last_cleanup
    now = time.time()

    with _lock:
        if now - _last_cleanup > _CLEANUP_INTERVAL:
            _cleanup(now)
            _last_cleanup = now

        ts, count = _requests.get(ip, (now, 0))

        if now - ts > AUTH_REQUEST_LINK_IP_WINDOW:
            # Окно истекло — начинаем новое
            _requests[ip] = (now, 1)
            return True, 0

        if count >= AUTH_REQUEST_LINK_IP_MAX:
            retry_after = max(1, int(AUTH_REQUEST_LINK_IP_WINDOW - (now - ts)))
            return False, retry_after

        _requests[ip] = (ts, count + 1)
        return True, 0
