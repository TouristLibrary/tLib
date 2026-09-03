"""
API tests for HTTP security headers.
All headers are verified on /health (JSON endpoint) and / (HTML endpoint).
"""
from __future__ import annotations

import re

import httpx
import pytest

ENDPOINTS = ["/health", "/", "/api/reports-count"]


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_x_content_type_options(client: httpx.Client, endpoint: str) -> None:
    resp = client.get(endpoint)
    value = resp.headers.get("x-content-type-options", "")
    assert value.lower() == "nosniff", (
        f"{endpoint}: expected X-Content-Type-Options: nosniff, got '{value}'"
    )


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_x_frame_options(client: httpx.Client, endpoint: str) -> None:
    resp = client.get(endpoint)
    value = resp.headers.get("x-frame-options", "")
    assert value.upper() == "SAMEORIGIN", (
        f"{endpoint}: expected X-Frame-Options: SAMEORIGIN, got '{value}'"
    )


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_csp_header_present(client: httpx.Client, endpoint: str) -> None:
    resp = client.get(endpoint)
    value = resp.headers.get("content-security-policy", "")
    assert value.strip(), (
        f"{endpoint}: Content-Security-Policy header is missing or empty"
    )


def test_hsts_header(client: httpx.Client) -> None:
    resp = client.get("/health")
    value = resp.headers.get("strict-transport-security", "")
    assert "max-age=31536000" in value, (
        f"Strict-Transport-Security expected 'max-age=31536000', got '{value}'"
    )


def test_no_server_version_leak(client: httpx.Client) -> None:
    resp = client.get("/health")
    server = resp.headers.get("server", "")
    version_pattern = re.compile(r"\d+\.\d+")
    assert not version_pattern.search(server), (
        f"Server header leaks version information: '{server}'"
    )
