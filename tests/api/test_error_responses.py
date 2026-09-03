"""
API tests for error response handling.
"""
from __future__ import annotations

import httpx


def test_404_nonexistent_path(client: httpx.Client) -> None:
    resp = client.get("/api/nonexistent_endpoint_xyz")
    assert resp.status_code in (404, 405), (
        f"Expected 404 or 405 for nonexistent path, got {resp.status_code}"
    )


def test_405_post_to_get_endpoint(client: httpx.Client) -> None:
    resp = client.post("/health")
    assert resp.status_code == 405, (
        f"Expected 405 Method Not Allowed for POST /health, got {resp.status_code}"
    )


def test_get_search_returns_405(client: httpx.Client) -> None:
    resp = client.get("/api/search")
    assert resp.status_code == 405, (
        f"Expected 405 for GET /api/search (POST only), got {resp.status_code}"
    )


def test_path_traversal_blocked(client: httpx.Client) -> None:
    resp = client.get("/api/archive/../../etc/passwd/contents")
    assert resp.status_code in (400, 403, 404), (
        f"Expected 400/403/404 for path traversal attempt, got {resp.status_code}"
    )


def test_static_nonexistent_file(client: httpx.Client) -> None:
    resp = client.get("/js/nonexistent_file_xyz.js")
    assert resp.status_code == 404, (
        f"Expected 404 for nonexistent static file, got {resp.status_code}"
    )
