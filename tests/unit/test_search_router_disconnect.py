# Version 1.0 - 06.10.2026 12:45:04 GMT
# Unit tests: routers/search_router.py — обрыв соединения клиентом на приёме формы
# Описание: Клиент закрывает соединение, пока сервер читает тело POST /api/search
#           (ClientDisconnect из request.form()). Ожидается общий конверт success=False,
#           одна INFO-строка и ни одной записи ERROR: обрыв клиентом не ошибка приложения
#           и не должен попадать в critical.log с traceback.

from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI
from starlette.requests import Request

from routers.search_router import search_database


def _disconnected_request() -> Request:
    """POST /api/search, у которого ASGI receive сразу сообщает об обрыве."""
    application = FastAPI()
    # До лимитера дело не доходит: обрыв происходит на приёме формы
    application.state.heavy_query_limiter = None

    async def receive() -> dict:
        return {"type": "http.disconnect"}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/search",
        "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
        "query_string": b"",
        "client": ("1.2.3.4", 12345),
        "app": application,
    }
    return Request(scope, receive)


def test_client_disconnect_logged_as_info_without_error(caplog):
    caplog.set_level(logging.INFO)

    result = asyncio.run(search_database(_disconnected_request()))

    assert result["success"] is False
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("клиент закрыл соединение" in r.getMessage() for r in caplog.records)
