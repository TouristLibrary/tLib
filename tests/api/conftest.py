"""
Pytest fixtures for TlibWebApp API tests.

Usage:
    BASE_URL=https://your-server.example.com python -m pytest tests/api -v
"""
from __future__ import annotations

import os
import warnings

import httpx
import pytest

from _constants import SAMPLE_REPORT  # noqa: E402


@pytest.fixture(scope="session")
def base_url() -> str:
    url = os.environ.get("BASE_URL", "")
    if not url:
        pytest.exit(
            "BASE_URL environment variable is required.\n"
            "Example: BASE_URL=https://myserver.example.com python -m pytest tests/api -v"
        )
    if not url.startswith("https://"):
        warnings.warn(
            f"BASE_URL '{url}' does not use HTTPS. "
            "Production server is expected to be HTTPS-only.",
            stacklevel=1,
        )
    return url.rstrip("/")


@pytest.fixture(scope="session")
def client(base_url: str) -> httpx.Client:
    with httpx.Client(
        base_url=base_url,
        timeout=15.0,
        follow_redirects=True,
    ) as c:
        yield c


@pytest.fixture(scope="session")
def sample_archive_id(client: httpx.Client) -> str:
    """
    Проверяет наличие посевного zip-отчёта 00001-TST на стенде.
    Если отчёт недоступен — предупреждает и пропускает зависимые happy-path тесты
    с явной причиной. На правильно засеянном стенде (например your-server.example.com)
    1-TST всегда доступен и кеш тёплый после E2E-прогона.
    """
    probe = client.get(f"/api/archive/{SAMPLE_REPORT}/contents", timeout=30.0)
    if probe.status_code == 200:
        return SAMPLE_REPORT
    warnings.warn(
        f"СИД ОТСУТСТВУЕТ: {SAMPLE_REPORT} — /api/archive/{SAMPLE_REPORT}/contents "
        f"вернул {probe.status_code}. Happy-path тесты архива/кеша будут пропущены. "
        "Засейте фикстуру через File Watcher (data.up/20_go/).",
        stacklevel=2,
    )
    pytest.skip(
        f"Сид {SAMPLE_REPORT} отсутствует на стенде (HTTP {probe.status_code}) — "
        "happy-path тесты архива/кеша пропущены; засейте фикстуру."
    )
