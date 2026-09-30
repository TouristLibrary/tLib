# Version 1.1 - 30.09.2026 15:27:03 GMT
# Unit tests for middlewares/rate_limit.py
# Описание: Проверяет, что PNG-страницы PDF-вьюера (/cache/) — лёгкая статика: не расходуют
#           API-лимит, из которого живёт heartbeat /api/png/.../pages, и не занимают слоты
#           concurrent-лимита. RateLimitMiddleware оборачивает тривиальное ASGI-приложение,
#           которое в момент запроса снимает active_connections — так виден счётчик «в полёте».
# Изменения v1.1: RATE_LIMIT_EXCEEDED получает путь запроса (endpoint).

from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse

import middlewares.rate_limit as rate_limit_module
from middlewares.rate_limit import RateLimitMiddleware

# IP, с которым ходит TestClient
CLIENT_IP = "testclient"

# Заведомо малый API-лимит: запросов в тесте больше, чем он допускает
API_LIMIT = 2


def _build():
    """Middleware вокруг приложения, которое на каждый запрос запоминает active_connections."""
    seen: list[int] = []

    async def inner(scope, receive, send):
        seen.append(mw.active_connections.get(CLIENT_IP, 0))
        await PlainTextResponse("ok")(scope, receive, send)

    mw = RateLimitMiddleware(inner, requests_per_minute=API_LIMIT)
    return mw, seen


class TestCacheIsLightStatic:
    def test_cache_not_limited_by_api_limit(self):
        mw, _ = _build()
        client = TestClient(mw)

        statuses = [client.get("/cache/09582/09582-png/09582_0001.png").status_code
                    for _ in range(API_LIMIT + 3)]

        assert statuses == [200] * (API_LIMIT + 3)
        # API-счётчик не тронут: heartbeat /pages после пролистывания не получит 429
        assert CLIENT_IP not in mw.requests
        assert mw.static_requests[CLIENT_IP][1] == API_LIMIT + 3

    def test_api_still_limited(self):
        # Контроль: тот же лимит на API-путях по-прежнему срабатывает
        mw, _ = _build()
        client = TestClient(mw)

        statuses = [client.get("/api/png/09582/09582-png/pages").status_code
                    for _ in range(API_LIMIT + 1)]

        assert statuses == [200] * API_LIMIT + [429]

    def test_cache_does_not_hold_concurrent_slot(self):
        mw, seen = _build()
        client = TestClient(mw)

        client.get("/cache/09582/09582-png/09582_0001.png")
        # Контроль: тяжёлая статика в полёте занимает слот — значит, замер рабочий
        client.get("/data/09582.zip")

        assert seen == [0, 1]
        assert mw.active_connections.get(CLIENT_IP, 0) == 0


class TestRateLimitLogsEndpoint:
    def test_api_429_logs_request_path(self, monkeypatch):
        # По endpoint видно, кого режет лимит: heartbeat /pages, PNG /cache/ или поиск
        calls = []
        monkeypatch.setattr(
            rate_limit_module.security_logger, "log_rate_limit_exceeded",
            lambda ip, endpoint=None: calls.append((ip, endpoint)),
        )
        mw, _ = _build()
        client = TestClient(mw)
        path = "/api/png/09582/09582-png/pages"

        for _ in range(API_LIMIT + 1):
            client.get(path)

        assert calls == [(CLIENT_IP, path)]
