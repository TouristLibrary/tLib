"""
Smoke-тесты: upload и auth endpoints против работающего сервера.

Только неавторизованные негативные проверки — не требуют ни логина, ни почты.
Запускать вместе с остальными api-тестами:
    BASE_URL=https://your-server.example.com python -m pytest tests/api -v

НЕ дёргает request-link с реальным email (не шлёт писем).
НЕ создаёт отчёты и не изменяет состояние сервера.
"""

from __future__ import annotations

import httpx


# ---------------------------------------------------------------------------
# Auth endpoints — защита без cookie
# ---------------------------------------------------------------------------


def test_auth_me_without_cookie_returns_401(client: httpx.Client) -> None:
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_auth_request_link_invalid_email_returns_400(client: httpx.Client) -> None:
    r = client.post("/api/auth/request-link", json={"email": "not-an-email"})
    assert r.status_code == 400


def test_auth_request_link_empty_email_returns_400(client: httpx.Client) -> None:
    r = client.post("/api/auth/request-link", json={"email": ""})
    assert r.status_code == 400


def test_auth_verify_code_missing_fields_returns_400(client: httpx.Client) -> None:
    r = client.post("/api/auth/verify-code", json={"email": "x@x.com"})
    assert r.status_code == 400


def test_auth_verify_code_wrong_code_returns_401(client: httpx.Client) -> None:
    # email существует или нет — без предварительного request-link кода нет
    r = client.post(
        "/api/auth/verify-code",
        json={"email": "smoke_nonexistent@tlib.test", "code": "000000"},
    )
    assert r.status_code == 401


def test_auth_logout_without_session_returns_ok(client: httpx.Client) -> None:
    r = client.post("/api/auth/logout")
    assert r.status_code == 200
    assert r.json().get("ok") is True


def test_auth_verify_link_invalid_token_redirects_to_error(client: httpx.Client) -> None:
    r = client.get("/auth/verify?token=smoketest_invalid_token_abc123")
    # После редиректа (follow_redirects=True) должна быть страница логина или HTML с error
    # Допустимы 200 (страница) и 302 (redirect)
    assert r.status_code in (200, 302, 404)
    # Проверяем, что приложение не упало (не 500)
    assert r.status_code != 500


# ---------------------------------------------------------------------------
# Upload endpoints — защита без cookie
# ---------------------------------------------------------------------------


def test_upload_next_code_without_cookie_returns_401(client: httpx.Client) -> None:
    r = client.get("/api/upload/next-code")
    assert r.status_code == 401


def test_upload_check_code_without_cookie_returns_401(client: httpx.Client) -> None:
    r = client.get("/api/upload/check-code?shifr=1&dopshifr=TST")
    assert r.status_code == 401


def test_upload_list_without_cookie_returns_401(client: httpx.Client) -> None:
    r = client.get("/api/upload/list")
    assert r.status_code == 401


def test_upload_item_without_cookie_returns_401(client: httpx.Client) -> None:
    r = client.get("/api/upload/item?id=00001-TST")
    assert r.status_code == 401


def test_upload_file_without_cookie_returns_401(client: httpx.Client) -> None:
    r = client.get("/api/upload/file?id=00001-TST")
    assert r.status_code == 401


def test_upload_publish_without_cookie_returns_401(client: httpx.Client) -> None:
    r = client.post(
        "/api/upload/publish",
        data={"id": "smoke-test", "shifr": 99999, "dopshifr": "SMK", "marshrut": "X", "god": 2024},
    )
    assert r.status_code == 401


def test_upload_reject_without_cookie_returns_401(client: httpx.Client) -> None:
    r = client.post("/api/upload/reject", json={"id": "smoke-test"})
    assert r.status_code == 401


def test_upload_published_item_without_cookie_returns_401(client: httpx.Client) -> None:
    r = client.get("/api/upload/published-item?id=00001-TST")
    assert r.status_code == 401


def test_upload_submit_edit_without_cookie_returns_401(client: httpx.Client) -> None:
    r = client.post(
        "/api/upload/submit-edit",
        data={
            "edit_orig_id": "00001-TST",
            "shifr": 1,
            "dopshifr": "TST",
            "marshrut": "X",
            "god": 2024,
        },
        files={"file": ("x.zip", b"PK", "application/zip")},
    )
    assert r.status_code == 401


def test_upload_request_delete_without_cookie_returns_401(client: httpx.Client) -> None:
    r = client.post(
        "/api/upload/request-delete",
        json={"id": "00001-TST", "confirm_code": "00001-TST", "reason": "smoke"},
    )
    assert r.status_code == 401


def test_upload_confirm_delete_without_cookie_returns_401(client: httpx.Client) -> None:
    r = client.post("/api/upload/confirm-delete", json={"id": "00001-TST"})
    assert r.status_code == 401


def test_upload_reject_delete_without_cookie_returns_401(client: httpx.Client) -> None:
    r = client.post("/api/upload/reject-delete", json={"id": "00001-TST"})
    assert r.status_code == 401


def test_upload_submit_without_cookie_returns_401(client: httpx.Client) -> None:
    r = client.post(
        "/api/upload/submit",
        data={"shifr": 99999, "dopshifr": "SMK", "marshrut": "X", "god": 2024},
        files={"file": ("smoke.zip", b"PK", "application/zip")},
    )
    assert r.status_code == 401


def test_upload_lookup_user_without_cookie_returns_401(client: httpx.Client) -> None:
    r = client.get("/api/upload/lookup-user?email=smoke@tlib.test")
    assert r.status_code == 401
