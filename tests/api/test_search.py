"""
API tests for POST /api/search endpoint.
"""
from __future__ import annotations

import httpx


def test_search_empty_returns_results(client: httpx.Client) -> None:
    resp = client.post("/api/search", data={})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    assert isinstance(body.get("data"), list)


def test_search_by_year(client: httpx.Client) -> None:
    resp = client.post("/api/search", data={"ГодС": "2024"})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    rows = body["data"]
    assert len(rows) > 0, "Expected results for year >= 2024"
    for row in rows:
        year_value = row.get("Год")
        if year_value is not None:
            assert int(year_value) >= 2024, (
                f"Row year {year_value} is less than 2024: {row}"
            )


def test_search_specific_report(client: httpx.Client) -> None:
    resp = client.post("/api/search", data={"Шифр": "1", "ДопШифр": "TST"})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    rows = body["data"]
    assert len(rows) == 1, f"Expected exactly 1 result for 1-TST, got {len(rows)}"
    route = rows[0].get("Маршрут", "") or rows[0].get("маршрут", "")
    assert "1-TST" in route or "TST" in str(rows[0]), (
        f"Result does not appear to be 1-TST: {rows[0]}"
    )


def test_search_not_found(client: httpx.Client) -> None:
    resp = client.post("/api/search", data={"Шифр": "99999"})
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("success") is True
    assert body["data"] == [], f"Expected empty results for Шифр=99999, got: {body['data']}"


def test_search_xss_input_safe(client: httpx.Client) -> None:
    payload = "<script>alert(1)</script>"
    resp = client.post("/api/search", data={"Маршрут": payload})
    assert resp.status_code == 200, f"Server returned {resp.status_code} on XSS input"
    body = resp.json()
    assert body.get("success") is True
    for row in body.get("data", []):
        for value in row.values():
            assert "<script>" not in str(value), (
                f"Raw <script> tag found in response row: {row}"
            )


def test_search_sql_injection_safe(client: httpx.Client) -> None:
    payload = "' OR '1'='1"
    resp = client.post("/api/search", data={"Автор": payload})
    assert resp.status_code == 200, f"Server returned {resp.status_code} on SQL injection input"
    body = resp.json()
    assert body.get("success") is True
    total = body.get("count", len(body.get("data", [])))
    assert isinstance(total, int)
