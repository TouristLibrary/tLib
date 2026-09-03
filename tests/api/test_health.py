"""
API tests for GET /health endpoint.
"""
from __future__ import annotations

from datetime import date

import httpx


def test_health_returns_200(client: httpx.Client) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body
    assert "timestamp" in body
    assert "checks" in body


def test_health_checks_structure(client: httpx.Client) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    checks = resp.json()["checks"]
    for key in ("database", "db_watcher", "file_watcher"):
        assert key in checks, f"Missing key in checks: {key}"
        assert "status" in checks[key], f"checks.{key} has no 'status' field"


def test_health_timestamp_is_recent(client: httpx.Client) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    timestamp: str = resp.json()["timestamp"]
    today = date.today().isoformat()
    assert today in timestamp, (
        f"Health timestamp '{timestamp}' does not contain today's date '{today}'. "
        "Server may be stale or clock is wrong."
    )


def test_health_does_not_leak_db_path(client: httpx.Client) -> None:
    """GET /health не должен раскрывать физический путь к файлу базы данных."""
    resp = client.get("/health")
    assert resp.status_code in (200, 503)
    body = resp.json()
    db_check = body.get("checks", {}).get("database", {})
    assert "path" not in db_check, (
        f"/health раскрывает путь к БД в checks.database: {db_check}"
    )
    # Убеждаемся, что строка с путём не просочилась в поле message
    message = db_check.get("message", "")
    assert "/" not in message and "\\" not in message, (
        f"/health содержит путь в checks.database.message: '{message}'"
    )
