"""
API tests for reference list endpoints and reports count.
"""
from __future__ import annotations

import httpx
import pytest


@pytest.mark.parametrize("endpoint", [
    "/api/dopshifr-list",
    "/api/raion-obshiy-list",
    "/api/tip-list",
    "/api/kategoria-s-list",
    "/api/kategoria-po-list",
])
def test_reference_list_nonempty(client: httpx.Client, endpoint: str) -> None:
    resp = client.get(endpoint)
    assert resp.status_code == 200, f"{endpoint} returned {resp.status_code}"
    body = resp.json()
    assert body.get("success") is True, f"{endpoint} body: {body}"
    data = body.get("data")
    assert isinstance(data, list), f"{endpoint} 'data' is not a list: {data}"
    assert len(data) > 0, f"{endpoint} returned empty list"


def test_reports_count(client: httpx.Client) -> None:
    resp = client.get("/api/reports-count")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    count = body.get("count")
    assert isinstance(count, int), f"'count' is not int: {count}"
    assert count > 0, f"reports-count returned 0 or negative: {count}"


def test_reference_version(client: httpx.Client) -> None:
    resp = client.get("/api/reference-version")
    assert resp.status_code == 200, f"/api/reference-version returned {resp.status_code}"
    body = resp.json()
    assert body.get("success") is True, f"reference-version body: {body}"
    version = body.get("version")
    assert version is not None, "reference-version не вернул поле 'version'"
