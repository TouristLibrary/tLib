"""
API tests for rate limiting behaviour.

The server allows up to 300 requests/min per IP, so we only test the
positive (below-threshold) scenario to avoid polluting the rate limit counter.
"""
from __future__ import annotations

import threading

import httpx


def test_single_request_not_rate_limited(client: httpx.Client) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200, (
        f"Single request to /health was rate-limited or failed: {resp.status_code}"
    )
    assert "retry-after" not in resp.headers, (
        "Retry-After header present on a single request -- unexpectedly rate-limited"
    )


def test_five_concurrent_requests_ok(base_url: str) -> None:
    results: list[int] = []

    def do_request() -> None:
        with httpx.Client(base_url=base_url, timeout=15.0) as c:
            results.append(c.get("/health").status_code)

    threads = [threading.Thread(target=do_request) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(s == 200 for s in results), (
        f"Some concurrent requests failed or were rate-limited: {results}"
    )
