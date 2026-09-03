# Version 1.3 - 21.06.2026
# Integration tests: auth endpoints + auth_db unit-блок
# Описание: In-process тесты для /api/auth/request-link, /api/auth/verify-code,
#           /auth/verify, /api/auth/logout, /api/auth/me, а также unit-тесты
#           приватных функций auth_db (_same_network, verify_magic_code, cleanup_expired).
#           Почта заглушена через Mailbox, auth.db — во временном tmp_path.
# Изменения v1.1: добавлены тесты для logout?all=1, пер-IP троттлинга request-link,
#           дневного лимита EMAIL_QUOTA и email_quota функций auth_db.
# Изменения v1.2: тест уровня URGENT для EMAIL_QUOTA; регресс-тест реальной доставки
#           send_admin_alert(EMAIL_QUOTA) через замену threading.Thread; тест нового
#           метода security_logger.log_email_quota_exceeded.
# Изменения v1.3: test_send_admin_alert_email_quota_actually_fires — добавлен try/finally
#           для очистки alerter._throttle["EMAIL_QUOTA"] после теста (изоляция состояния).

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

import routers.auth_router as arm
import services.auth.auth_db as adb
import services.auth.request_throttle as throttle_module
from config import AUTH_CODE_MAX_ATTEMPTS, AUTH_CODE_LENGTH
from tests.integration.helpers import insert_magic_link_direct, sha256hex


# ---------------------------------------------------------------------------
# /api/auth/request-link
# ---------------------------------------------------------------------------


class TestRequestLink:
    def test_invalid_json_returns_400(self, client, auth_db_path, mailbox):
        r = client.post(
            "/api/auth/request-link",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400

    def test_email_without_at_returns_400(self, client, auth_db_path, mailbox):
        r = client.post("/api/auth/request-link", json={"email": "notanemail"})
        assert r.status_code == 400

    def test_empty_email_returns_400(self, client, auth_db_path, mailbox):
        r = client.post("/api/auth/request-link", json={"email": ""})
        assert r.status_code == 400

    def test_success_returns_ok_and_sends_code(self, client, auth_db_path, mailbox):
        r = client.post("/api/auth/request-link", json={"email": "new@example.com"})
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert mailbox.last_code is not None
        assert len(mailbox.last_code) == AUTH_CODE_LENGTH
        assert mailbox.last_code.isdigit()

    def test_rate_limit_same_email_returns_429(self, client, auth_db_path, mailbox):
        email = "ratelimit@example.com"
        r1 = client.post("/api/auth/request-link", json={"email": email})
        assert r1.status_code == 200
        r2 = client.post("/api/auth/request-link", json={"email": email})
        assert r2.status_code == 429

    def test_different_emails_not_rate_limited(self, client, auth_db_path, mailbox):
        r1 = client.post("/api/auth/request-link", json={"email": "alice@example.com"})
        r2 = client.post("/api/auth/request-link", json={"email": "bob@example.com"})
        assert r1.status_code == 200
        assert r2.status_code == 200

    def test_blocked_user_returns_403(self, client, auth_db_path, mailbox):
        email = "blocked@example.com"
        adb.find_or_create_user(email, "Blocked")
        adb.set_user_active(email, 0)
        r = client.post("/api/auth/request-link", json={"email": email})
        assert r.status_code == 403

    def test_email_failure_returns_500_without_internal_details(
        self, client, auth_db_path, mailbox, monkeypatch
    ):
        def fail_send(email, token, redirect="", code=""):
            raise RuntimeError("SMTP timeout: connection refused")

        monkeypatch.setattr(arm, "send_magic_link", fail_send)
        r = client.post("/api/auth/request-link", json={"email": "fail@example.com"})
        assert r.status_code == 500
        # Внутренние детали не должны утекать клиенту
        assert "SMTP" not in r.text
        assert "timeout" not in r.text
        assert "Traceback" not in r.text

    def test_invalid_redirect_stripped(self, client, auth_db_path, mailbox):
        r = client.post(
            "/api/auth/request-link",
            json={"email": "redir@example.com", "redirect": "//evil.com"},
        )
        assert r.status_code == 200
        # Redirect должен быть очищен до "" перед передачей в send_magic_link
        call = mailbox.last_call_of("magic_link")
        assert call is not None
        redirect_arg = call[3]
        assert redirect_arg == ""

    def test_valid_relative_redirect_preserved(self, client, auth_db_path, mailbox):
        r = client.post(
            "/api/auth/request-link",
            json={"email": "goodredir@example.com", "redirect": "/upload.html"},
        )
        assert r.status_code == 200
        call = mailbox.last_call_of("magic_link")
        assert call is not None
        assert call[3] == "/upload.html"


# ---------------------------------------------------------------------------
# /api/auth/verify-code
# ---------------------------------------------------------------------------


class TestVerifyCode:
    def test_correct_code_returns_ok_and_sets_cookie(self, client, auth_db_path, mailbox):
        email = "codeverify@example.com"
        client.post("/api/auth/request-link", json={"email": email})
        code = mailbox.last_code
        r = client.post("/api/auth/verify-code", json={"email": email, "code": code})
        assert r.status_code == 200
        assert r.json().get("ok") is True
        # Cookie установлена — me должен вернуть данные
        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == email

    def test_wrong_code_returns_401(self, client, auth_db_path, mailbox):
        email = "wrongcode@example.com"
        client.post("/api/auth/request-link", json={"email": email})
        correct = mailbox.last_code
        wrong = str((int(correct) + 1) % 10 ** AUTH_CODE_LENGTH).zfill(AUTH_CODE_LENGTH)
        r = client.post("/api/auth/verify-code", json={"email": email, "code": wrong})
        assert r.status_code == 401

    def test_max_attempts_burns_code(self, client, auth_db_path, mailbox):
        email = "burncode@example.com"
        client.post("/api/auth/request-link", json={"email": email})
        correct = mailbox.last_code
        wrong = str((int(correct) + 1) % 10 ** AUTH_CODE_LENGTH).zfill(AUTH_CODE_LENGTH)

        # AUTH_CODE_MAX_ATTEMPTS неверных попыток → код сжигается на последней
        for _ in range(AUTH_CODE_MAX_ATTEMPTS):
            client.post("/api/auth/verify-code", json={"email": email, "code": wrong})

        # Даже верный код теперь не принимается
        r = client.post("/api/auth/verify-code", json={"email": email, "code": correct})
        assert r.status_code == 401

    def test_code_reuse_fails(self, client, auth_db_path, mailbox):
        email = "reuse@example.com"
        client.post("/api/auth/request-link", json={"email": email})
        code = mailbox.last_code
        r1 = client.post("/api/auth/verify-code", json={"email": email, "code": code})
        assert r1.status_code == 200
        # Повторное использование — токен удалён из БД
        r2 = client.post("/api/auth/verify-code", json={"email": email, "code": code})
        assert r2.status_code == 401

    def test_expired_code_returns_401(self, client, auth_db_path, mailbox):
        email = "expired@example.com"
        adb.find_or_create_user(email, "Expired")
        code = "654321"
        insert_magic_link_direct(auth_db_path, email, code, expired=True)
        r = client.post("/api/auth/verify-code", json={"email": email, "code": code})
        assert r.status_code == 401

    def test_missing_fields_returns_400(self, client, auth_db_path, mailbox):
        r = client.post("/api/auth/verify-code", json={"email": "x@x.com"})
        assert r.status_code == 400

    def test_blocked_user_after_code_returns_403(self, client, auth_db_path, mailbox):
        email = "blocked2@example.com"
        # Создаём пользователя и делаем magic link напрямую
        adb.find_or_create_user(email, "Test")
        adb.set_user_active(email, 0)
        code = "111111"
        insert_magic_link_direct(auth_db_path, email, code)
        r = client.post("/api/auth/verify-code", json={"email": email, "code": code})
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /auth/verify  (link click)
# ---------------------------------------------------------------------------


class TestVerifyLink:
    def test_valid_token_redirects_with_cookie(self, client, auth_db_path, mailbox):
        email = "linkverify@example.com"
        client.post("/api/auth/request-link", json={"email": email, "redirect": "/upload.html"})
        token = mailbox.last_token
        assert token is not None
        r = client.get(f"/auth/verify?token={token}&redirect=/upload.html", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers.get("location") == "/upload.html"
        cookie_header = r.headers.get("set-cookie", "")
        assert "session_token" in cookie_header

    def test_invalid_token_redirects_to_error(self, client, auth_db_path, mailbox):
        r = client.get("/auth/verify?token=definitely_invalid_token", follow_redirects=False)
        assert r.status_code == 302
        assert "error=expired" in r.headers.get("location", "")

    def test_missing_token_redirects_to_error(self, client, auth_db_path, mailbox):
        r = client.get("/auth/verify", follow_redirects=False)
        assert r.status_code == 302
        assert "error=expired" in r.headers.get("location", "")

    def test_open_redirect_double_slash_stripped(self, client, auth_db_path, mailbox):
        email = "openredir@example.com"
        client.post("/api/auth/request-link", json={"email": email})
        token = mailbox.last_token
        r = client.get(f"/auth/verify?token={token}&redirect=//evil.com", follow_redirects=False)
        assert r.status_code == 302
        location = r.headers.get("location", "")
        assert "evil.com" not in location

    def test_https_redirect_stripped(self, client, auth_db_path, mailbox):
        email = "httpsredir@example.com"
        client.post("/api/auth/request-link", json={"email": email})
        token = mailbox.last_token
        r = client.get(
            f"/auth/verify?token={token}&redirect=https://evil.com", follow_redirects=False
        )
        assert r.status_code == 302
        assert "evil.com" not in r.headers.get("location", "")

    def test_token_reuse_fails(self, client, auth_db_path, mailbox):
        email = "tokenreuse@example.com"
        client.post("/api/auth/request-link", json={"email": email})
        token = mailbox.last_token
        # Первое использование
        r1 = client.get(f"/auth/verify?token={token}", follow_redirects=False)
        assert r1.status_code == 302
        assert "error=expired" not in r1.headers.get("location", "")
        # Второе использование — токен удалён атомарно
        client.post("/api/auth/logout")
        r2 = client.get(f"/auth/verify?token={token}", follow_redirects=False)
        assert r2.status_code == 302
        assert "error=expired" in r2.headers.get("location", "")


# ---------------------------------------------------------------------------
# /api/auth/logout  +  /api/auth/me
# ---------------------------------------------------------------------------


class TestLogoutAndMe:
    def test_me_without_cookie_returns_401(self, client, auth_db_path, mailbox):
        r = client.get("/api/auth/me")
        assert r.status_code == 401

    def test_me_with_valid_session_returns_user(self, client, logged_in_user):
        r = client.get("/api/auth/me")
        assert r.status_code == 200
        data = r.json()
        assert data["email"] == logged_in_user
        assert "id" in data
        assert "role" in data

    def test_logout_invalidates_session(self, client, logged_in_user):
        # До logout — авторизованы
        me_before = client.get("/api/auth/me")
        assert me_before.status_code == 200
        # Logout
        r = client.post("/api/auth/logout")
        assert r.status_code == 200
        assert r.json().get("ok") is True
        # После logout — 401
        me_after = client.get("/api/auth/me")
        assert me_after.status_code == 401

    def test_logout_without_session_returns_ok(self, client, auth_db_path, mailbox):
        r = client.post("/api/auth/logout")
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_admin_role_visible_in_me(self, client, logged_in_admin):
        r = client.get("/api/auth/me")
        assert r.status_code == 200
        assert r.json()["role"] == "admin"


# ---------------------------------------------------------------------------
# Unit-тесты функций auth_db (работают напрямую без HTTP)
# ---------------------------------------------------------------------------


class TestSameNetwork:
    """_same_network: проверка сетевого совпадения IP для admin-сессий."""

    def _fn(self, a, b):
        from services.auth.auth_db import _same_network as fn
        return fn(a, b)

    def test_same_ipv4_24_subnet(self):
        assert self._fn("192.168.1.1", "192.168.1.254") is True

    def test_different_ipv4_24_subnet(self):
        assert self._fn("192.168.1.1", "192.168.2.1") is False

    def test_ipv4_mapped_ipv6_treated_as_ipv4(self):
        # ::ffff:192.168.1.1 — это IPv4-mapped, должен совпадать с 192.168.1.2
        assert self._fn("::ffff:192.168.1.1", "192.168.1.2") is True

    def test_ipv4_mapped_ipv6_cross_subnet_false(self):
        assert self._fn("::ffff:192.168.1.1", "::ffff:192.168.2.1") is False

    def test_ipv6_same_64_prefix(self):
        assert self._fn("2001:db8::1", "2001:db8::ffff") is True

    def test_ipv6_different_64_prefix(self):
        assert self._fn("2001:db8:0:1::1", "2001:db8:0:2::1") is False

    def test_both_empty_true(self):
        assert self._fn("", "") is True

    def test_one_empty_false(self):
        assert self._fn("", "192.168.1.1") is False

    def test_garbage_strings_equal(self):
        assert self._fn("testclient", "testclient") is True

    def test_garbage_strings_differ(self):
        assert self._fn("testclient", "otherclient") is False


class TestVerifyMagicCodeUnit:
    """verify_magic_code: атомарность, счётчик попыток, burn-on-max."""

    def test_correct_code_returns_email(self, auth_db_path):
        email = "unit_correct@example.com"
        adb.find_or_create_user(email, "Test")
        code = "123456"
        insert_magic_link_direct(auth_db_path, email, code)
        result = adb.verify_magic_code(email, code)
        assert result == email

    def test_wrong_code_increments_attempts(self, auth_db_path):
        email = "unit_wrong@example.com"
        adb.find_or_create_user(email, "Test")
        code = "234567"
        insert_magic_link_direct(auth_db_path, email, code)
        result = adb.verify_magic_code(email, "000000")
        assert result is None
        # Запись ещё существует с attempts=1
        conn = sqlite3.connect(auth_db_path)
        row = conn.execute("SELECT attempts FROM magic_links WHERE email=?", (email,)).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 1

    def test_max_attempts_deletes_record(self, auth_db_path):
        email = "unit_burn@example.com"
        adb.find_or_create_user(email, "Test")
        code = "345678"
        insert_magic_link_direct(auth_db_path, email, code)
        wrong = str((int(code) + 1) % 10 ** AUTH_CODE_LENGTH).zfill(AUTH_CODE_LENGTH)
        for _ in range(AUTH_CODE_MAX_ATTEMPTS):
            adb.verify_magic_code(email, wrong)
        # Запись удалена
        conn = sqlite3.connect(auth_db_path)
        row = conn.execute("SELECT 1 FROM magic_links WHERE email=?", (email,)).fetchone()
        conn.close()
        assert row is None

    def test_correct_code_deletes_record_atomically(self, auth_db_path):
        email = "unit_atomic@example.com"
        adb.find_or_create_user(email, "Test")
        code = "456789"
        insert_magic_link_direct(auth_db_path, email, code)
        adb.verify_magic_code(email, code)
        # После верного использования запись удалена
        conn = sqlite3.connect(auth_db_path)
        row = conn.execute("SELECT 1 FROM magic_links WHERE email=?", (email,)).fetchone()
        conn.close()
        assert row is None


class TestCleanupExpired:
    def test_cleanup_removes_expired_links_and_sessions(self, auth_db_path):
        email = "cleanup@example.com"
        adb.find_or_create_user(email, "Test")

        # Вставляем просроченный magic link
        insert_magic_link_direct(auth_db_path, email, "999999", expired=True)

        # Создаём просроченную сессию напрямую
        now = datetime.now(timezone.utc)
        past = (now - timedelta(hours=1)).isoformat()
        user = adb.get_user_by_email(email)
        conn = sqlite3.connect(auth_db_path)
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at, created_at, ip)"
            " VALUES (?, ?, ?, ?, '')",
            (sha256hex("expired_token"), user["id"], past, now.isoformat()),
        )
        conn.commit()
        conn.close()

        adb.cleanup_expired()

        conn = sqlite3.connect(auth_db_path)
        links = conn.execute("SELECT 1 FROM magic_links WHERE email=?", (email,)).fetchone()
        sessions = conn.execute(
            "SELECT 1 FROM sessions WHERE token_hash=?", (sha256hex("expired_token"),)
        ).fetchone()
        conn.close()

        assert links is None, "Просроченный magic link должен быть удалён"
        assert sessions is None, "Просроченная сессия должна быть удалена"

    def test_cleanup_keeps_valid_records(self, auth_db_path):
        email = "keepvalid@example.com"
        adb.find_or_create_user(email, "Test")
        # Активный magic link
        code = "777777"
        insert_magic_link_direct(auth_db_path, email, code, expired=False)

        adb.cleanup_expired()

        conn = sqlite3.connect(auth_db_path)
        row = conn.execute("SELECT 1 FROM magic_links WHERE email=?", (email,)).fetchone()
        conn.close()
        assert row is not None, "Действующий magic link не должен быть удалён"


# ---------------------------------------------------------------------------
# Logout?all=1 — выход со всех устройств
# ---------------------------------------------------------------------------


class TestLogoutAll:
    def test_logout_all_invalidates_all_sessions(self, client, logged_in_user, auth_db_path):
        """logout?all=1 должен аннулировать все сессии пользователя."""
        # Создаём вторую сессию напрямую через auth_db
        user = adb.get_user_by_email(logged_in_user)
        assert user is not None
        token2 = adb.create_session(user["id"], ip="127.0.0.2")

        # Убеждаемся, что текущая сессия клиента валидна
        me1 = client.get("/api/auth/me")
        assert me1.status_code == 200

        # Убеждаемся, что обе сессии существуют
        conn = sqlite3.connect(auth_db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE user_id=?", (user["id"],)
        ).fetchone()[0]
        conn.close()
        assert count >= 2, "Должно быть минимум 2 сессии"

        # Выходим со всех устройств
        r = client.post("/api/auth/logout?all=1")
        assert r.status_code == 200
        assert r.json().get("ok") is True

        # Первый клиент разлогинен (cookie удалена + сессия удалена)
        me_after = client.get("/api/auth/me")
        assert me_after.status_code == 401

        # Вторая сессия тоже аннулирована
        session2_user = adb.get_user_by_session(token2)
        assert session2_user is None, "Вторая сессия должна быть удалена"

    def test_logout_single_keeps_other_sessions(self, client, logged_in_user, auth_db_path):
        """Обычный logout удаляет только текущую сессию, оставляя другие."""
        user = adb.get_user_by_email(logged_in_user)
        token2 = adb.create_session(user["id"], ip="127.0.0.2")

        # Обычный logout
        r = client.post("/api/auth/logout")
        assert r.status_code == 200

        # Вторая сессия должна остаться
        session2_user = adb.get_user_by_session(token2)
        assert session2_user is not None, "Вторая сессия должна остаться"

    def test_logout_all_without_session_returns_ok(self, client, auth_db_path, mailbox):
        """logout?all=1 без валидного cookie возвращает ok (как обычный logout)."""
        r = client.post("/api/auth/logout?all=1")
        assert r.status_code == 200
        assert r.json().get("ok") is True


# ---------------------------------------------------------------------------
# Пер-IP троттлинг request-link
# ---------------------------------------------------------------------------


class TestRequestLinkIpThrottle:
    def test_ip_throttle_blocks_after_max(self, client, auth_db_path, mailbox, monkeypatch):
        """После AUTH_REQUEST_LINK_IP_MAX запросов с одного IP следующий должен вернуть 429."""
        from config import AUTH_REQUEST_LINK_IP_MAX

        # Разные email, чтобы не задействовать пер-email лимит 60 с
        for i in range(AUTH_REQUEST_LINK_IP_MAX):
            r = client.post("/api/auth/request-link", json={"email": f"ipthrottle{i}@example.com"})
            assert r.status_code == 200, f"Запрос {i+1} должен быть разрешён"

        r = client.post("/api/auth/request-link", json={"email": "ipthrottle_blocked@example.com"})
        assert r.status_code == 429
        data = r.json()
        assert data.get("reason") == "ip_throttle"
        assert "retry_after" in data
        assert data["retry_after"] > 0
        assert "мин" in data.get("error", "")

    def test_ip_throttle_429_has_structured_fields(self, client, auth_db_path, mailbox, monkeypatch):
        """429 от IP-троттлинга содержит reason и retry_after."""
        from config import AUTH_REQUEST_LINK_IP_MAX

        for i in range(AUTH_REQUEST_LINK_IP_MAX):
            client.post("/api/auth/request-link", json={"email": f"ipfields{i}@example.com"})

        r = client.post("/api/auth/request-link", json={"email": "ipfields_check@example.com"})
        assert r.status_code == 429
        body = r.json()
        assert "reason" in body
        assert "retry_after" in body
        assert "error" in body


# ---------------------------------------------------------------------------
# Дневной лимит request-link (email_quota)
# ---------------------------------------------------------------------------


class TestEmailQuota:
    def test_daily_cap_blocks_when_exceeded(self, client, auth_db_path, mailbox, monkeypatch):
        """При cap=1 второй запрос (разные email) возвращает 429 с reason=daily_cap."""
        monkeypatch.setattr(arm, "AUTH_EMAIL_DAILY_CAP", 1)

        r1 = client.post("/api/auth/request-link", json={"email": "quota1@example.com"})
        assert r1.status_code == 200

        r2 = client.post("/api/auth/request-link", json={"email": "quota2@example.com"})
        assert r2.status_code == 429
        data = r2.json()
        assert data.get("reason") == "daily_cap"
        assert "retry_after" in data
        assert data["retry_after"] > 0
        assert "00:00 UTC" in data.get("error", "") or "лимит" in data.get("error", "")

    def test_daily_cap_alert_sent(self, client, auth_db_path, mailbox, monkeypatch):
        """При достижении daily_cap должен вызываться send_admin_alert с EMAIL_QUOTA."""
        alerts_sent = []

        def capture_alert(event_type, **kwargs):
            alerts_sent.append(event_type)

        monkeypatch.setattr(arm, "AUTH_EMAIL_DAILY_CAP", 1)
        monkeypatch.setattr(arm, "send_admin_alert", capture_alert)

        client.post("/api/auth/request-link", json={"email": "alertquota1@example.com"})
        client.post("/api/auth/request-link", json={"email": "alertquota2@example.com"})

        assert "EMAIL_QUOTA" in alerts_sent

    def test_daily_cap_resets_on_new_day(self, auth_db_path, monkeypatch):
        """Счётчик сбрасывается при смене даты UTC."""
        import datetime as dt

        # Сохраняем вчерашнюю дату и count=999
        adb.set_setting("email_quota_date", "2000-01-01")
        adb.set_setting("email_quota_count", "999")

        # Функция должна разрешить запрос (дата не совпадает → сброс)
        assert adb.email_quota_remaining(cap=400) is True

    def test_daily_cap_remaining_false_when_exceeded(self, auth_db_path):
        """email_quota_remaining возвращает False если count >= cap."""
        today = datetime.now(timezone.utc).date().isoformat()
        adb.set_setting("email_quota_date", today)
        adb.set_setting("email_quota_count", "400")

        assert adb.email_quota_remaining(cap=400) is False

    def test_bump_increments_count(self, auth_db_path):
        """bump_email_quota увеличивает счётчик."""
        today = datetime.now(timezone.utc).date().isoformat()
        adb.set_setting("email_quota_date", today)
        adb.set_setting("email_quota_count", "5")

        adb.bump_email_quota()

        conn = sqlite3.connect(auth_db_path)
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key='email_quota_count'"
        ).fetchone()
        conn.close()
        assert int(row[0]) == 6

    def test_seconds_until_utc_midnight_positive(self):
        """seconds_until_utc_midnight всегда возвращает положительное значение."""
        assert adb.seconds_until_utc_midnight() > 0


# ---------------------------------------------------------------------------
# delete_all_sessions_for_token (unit)
# ---------------------------------------------------------------------------


class TestDeleteAllSessionsForToken:
    def test_deletes_all_sessions_for_user(self, auth_db_path):
        email = "dast@example.com"
        adb.find_or_create_user(email, "Test")
        user = adb.get_user_by_email(email)

        t1 = adb.create_session(user["id"], ip="1.2.3.4")
        t2 = adb.create_session(user["id"], ip="1.2.3.5")

        result = adb.delete_all_sessions_for_token(t1)
        assert result is True

        assert adb.get_user_by_session(t1) is None
        assert adb.get_user_by_session(t2) is None

    def test_returns_false_for_invalid_token(self, auth_db_path):
        assert adb.delete_all_sessions_for_token("nonexistent_token") is False


# ---------------------------------------------------------------------------
# Регресс-тесты: EMAIL_QUOTA — уровень, алерт-доставка, лог-метка
# ---------------------------------------------------------------------------


class TestEmailQuotaRegression:
    """Убеждаемся, что EMAIL_QUOTA зарегистрирован как URGENT и реально доходит до отправки."""

    def test_email_quota_alert_level_is_urgent(self):
        """EMAIL_QUOTA должен быть URGENT — иначе send_admin_alert делает ранний return."""
        from config import ALERT_LEVELS
        assert ALERT_LEVELS.get("EMAIL_QUOTA") == "URGENT", (
            "EMAIL_QUOTA должен быть URGENT, чтобы send_admin_alert отправлял письмо немедленно. "
            "ATTENTION → только суточный дайджест."
        )

    def test_send_admin_alert_email_quota_actually_fires(self, monkeypatch):
        """Проверяет, что send_admin_alert для EMAIL_QUOTA реально инициирует отправку письма.

        Замоканный threading.Thread фиксирует вызов start() — это доказывает,
        что не было раннего return (который бывает для ATTENTION-событий).
        """
        import threading
        import services.alerts.alerter as alerter_module

        thread_started = []

        class FakeThread:
            def __init__(self, target=None, args=(), daemon=False, **kw):
                self._target = target
                self._args = args

            def start(self):
                thread_started.append(True)

        # Очищаем троттлинг до вызова, чтобы не заблокировало от предыдущих тестов
        alerter_module._throttle.pop("EMAIL_QUOTA", None)

        monkeypatch.setattr(alerter_module, "collect_admin_emails", lambda: ["test@example.com"])
        monkeypatch.setattr(alerter_module.threading, "Thread", FakeThread)

        try:
            alerter_module.send_admin_alert("EMAIL_QUOTA", daily_cap=400)
            assert thread_started, (
                "send_admin_alert(EMAIL_QUOTA) не запустил поток отправки. "
                "Возможно, EMAIL_QUOTA больше не является URGENT."
            )
        finally:
            # Убираем запись о троттлинге, оставшуюся после _is_throttled внутри send_admin_alert,
            # чтобы не загрязнять глобальный alerter._throttle для последующих тестов.
            alerter_module._throttle.pop("EMAIL_QUOTA", None)

    def test_log_email_quota_exceeded_does_not_raise(self):
        """log_email_quota_exceeded не должен бросать исключений."""
        from logging_config import security_logger
        security_logger.log_email_quota_exceeded(ip="1.2.3.4", cap=400)

    def test_email_quota_log_uses_correct_event_type(self, monkeypatch):
        """Убеждаемся, что log_email_quota_exceeded записывает event_type=EMAIL_QUOTA,
        а не INVALID_REQUEST (как было до Fix 2)."""
        from logging_config import security_logger

        logged_events = []

        def capture_security_event(event_type, **kwargs):
            logged_events.append(event_type)

        import logging_config as lc
        monkeypatch.setattr(lc, "log_security_event", capture_security_event)
        # Вызываем через модуль, чтобы monkeypatch перехватил
        lc.security_logger.log_email_quota_exceeded(ip="1.2.3.4", cap=400)

        assert logged_events == ["EMAIL_QUOTA"], (
            f"Ожидался event_type=EMAIL_QUOTA, получен: {logged_events}"
        )
