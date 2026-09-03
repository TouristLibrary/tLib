# Version 1.6 - 24.06.2026 21:00:00 GMT
# Integration tests: admin endpoints
# Описание: In-process smoke-тесты для /api/admin/* и /admin.
#           Проверяют: 401 без сессии, 401 для обычного пользователя,
#           200 для администратора на ключевых endpoint.
#           Использует фикстуры из integration/conftest.py; auth_db_path патч применяется там.
# 1.1: DATABASE_PATH и DATA_DIRECTORY патчатся в status_service (перенесены из admin_router).
# 1.2: smoke-тесты панели управления пользователями (users, sessions, activate/deactivate/delete).
# 1.3: тесты ROOT_ADMIN_EMAIL 403 для deactivate и delete.
# 1.4: удалены delete-тесты (эндпоинт убран, удаление только через CLI).
# 1.5: тесты кнопки «Выйти» — is_current в сессиях, 403 на свою сессию, удаление чужой, 401.
# 1.6: удалены тесты /api/admin/sessions и /sessions/delete (эндпоинты убраны);
#      добавлены тесты новых полей /api/admin/users (is_root, is_self, last_login_ip, active_session_count)
#      и нового эндпоинта POST /api/admin/users/logout (завершение сессий; своя сессия сохраняется).

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.admin_router as admin_router_module
import services.admin.status_service as status_service_module
import services.auth.auth_db as auth_db_module
import services.auth.session_helpers as session_helpers_module
from routers.admin_router import router as admin_router
from routers.auth_router import router as auth_router
from services.auth.auth_db import create_session, find_or_create_user, set_user_role
from services.database.tlib_table_spec import build_create_table_sql
from config import AUTH_COOKIE_NAME, DATABASE_TABLE_NAME

from tests.integration.conftest import Mailbox, auth_db_path, mailbox


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture()
def admin_tlib_db(tmp_path, monkeypatch) -> str:
    """Создаёт временную tlib.db с пустой таблицей; патчит DATABASE_PATH в status_service."""
    db_path = str(tmp_path / "tlib_admin.db")
    conn = sqlite3.connect(db_path)
    conn.execute(build_create_table_sql(DATABASE_TABLE_NAME))
    conn.commit()
    conn.close()
    monkeypatch.setattr(status_service_module, "DATABASE_PATH", db_path)
    return db_path


@pytest.fixture()
def admin_dirs(tmp_path, monkeypatch) -> dict[str, Path]:
    """Создаёт и патчит директории, используемые admin endpoints."""
    dirs = {
        "data": tmp_path / "data",
        "go": tmp_path / "20_go",
        "processing": tmp_path / "30_processing",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    # DATA_DIRECTORY переехал в status_service
    monkeypatch.setattr(status_service_module, "DATA_DIRECTORY", str(dirs["data"]))
    # UPLOAD_GO_DIRECTORY / UPLOAD_PROCESSING_DIRECTORY нужны и router (pause/reindex) и service (growth)
    monkeypatch.setattr(admin_router_module, "UPLOAD_GO_DIRECTORY", str(dirs["go"]))
    monkeypatch.setattr(admin_router_module, "UPLOAD_PROCESSING_DIRECTORY", str(dirs["processing"]))
    monkeypatch.setattr(status_service_module, "UPLOAD_GO_DIRECTORY", str(dirs["go"]))
    monkeypatch.setattr(status_service_module, "UPLOAD_PROCESSING_DIRECTORY", str(dirs["processing"]))
    monkeypatch.setattr(admin_router_module, "ROOT_ADMIN_EMAIL", "")
    monkeypatch.setattr(session_helpers_module, "ROOT_ADMIN_EMAIL", "")
    return dirs


@pytest.fixture()
def admin_app_instance(auth_db_path, admin_tlib_db, admin_dirs) -> FastAPI:
    """
    Минимальное FastAPI-приложение с auth + admin роутерами.
    auth_db_path уже содержит патч на AUTH_DB_PATH — зависимость гарантирует порядок.
    """
    application = FastAPI()
    application.include_router(auth_router)
    application.include_router(admin_router)
    return application


@pytest.fixture()
def admin_client(admin_app_instance) -> TestClient:
    with TestClient(admin_app_instance, raise_server_exceptions=True) as c:
        yield c


def _do_login(client: TestClient, email: str, mailbox: Mailbox) -> None:
    r = client.post("/api/auth/request-link", json={"email": email})
    assert r.status_code == 200, f"request-link: {r.status_code} {r.text}"
    code = mailbox.last_code
    assert code
    r2 = client.post("/api/auth/verify-code", json={"email": email, "code": code})
    assert r2.status_code == 200, f"verify-code: {r2.status_code} {r2.text}"


# ---------------------------------------------------------------------------
# 401 без сессии
# ---------------------------------------------------------------------------


PROTECTED_ENDPOINTS = [
    ("GET", "/api/admin/status"),
    ("GET", "/api/admin/admins"),
    ("GET", "/api/admin/settings"),
    ("GET", "/api/admin/reindex-status"),
    ("GET", "/api/admin/users"),
]


class TestAdminUnauthorized:
    @pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
    def test_returns_401_without_session(self, admin_client, method, path):
        r = admin_client.request(method, path)
        assert r.status_code == 401, f"{method} {path} → {r.status_code}"

    def test_post_grant_returns_401_without_session(self, admin_client):
        r = admin_client.post("/api/admin/grant", json={"email": "x@x.com"})
        assert r.status_code == 401

    def test_post_revoke_returns_401_without_session(self, admin_client):
        r = admin_client.post("/api/admin/revoke", json={"emails": ["x@x.com"]})
        assert r.status_code == 401

    def test_post_pause_returns_401_without_session(self, admin_client):
        r = admin_client.post("/api/admin/pause")
        assert r.status_code == 401

    def test_post_reindex_returns_401_without_session(self, admin_client):
        r = admin_client.post("/api/admin/reindex")
        assert r.status_code == 401

    def test_post_settings_returns_401_without_session(self, admin_client):
        r = admin_client.post("/api/admin/settings", json={})
        assert r.status_code == 401

    def test_health_brief_accessible_without_auth(self, admin_client):
        """health-brief доступен без аутентификации."""
        r = admin_client.get("/api/admin/health-brief")
        assert r.status_code == 200
        assert "overall" in r.json()


# ---------------------------------------------------------------------------
# 401 для обычного пользователя (не-администратора)
# ---------------------------------------------------------------------------


class TestAdminForbiddenForRegularUser:
    def test_status_returns_401_for_regular_user(self, admin_client, mailbox):
        email = "regular_user@admin.test"
        _do_login(admin_client, email, mailbox)
        r = admin_client.get("/api/admin/status")
        assert r.status_code == 401

    def test_grant_returns_401_for_regular_user(self, admin_client, mailbox):
        email = "regular_user2@admin.test"
        _do_login(admin_client, email, mailbox)
        r = admin_client.post("/api/admin/grant", json={"email": "x@x.com"})
        assert r.status_code == 401

    def test_admins_list_returns_401_for_regular_user(self, admin_client, mailbox):
        email = "regular_user3@admin.test"
        _do_login(admin_client, email, mailbox)
        r = admin_client.get("/api/admin/admins")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# 200 для администратора
# ---------------------------------------------------------------------------


class TestAdminAuthorized:
    def _login_admin(self, client, email, mailbox):
        _do_login(client, email, mailbox)
        set_user_role(email, "admin")

    def test_status_returns_200(self, admin_client, mailbox):
        email = "admin1@admin.test"
        self._login_admin(admin_client, email, mailbox)
        r = admin_client.get("/api/admin/status")
        assert r.status_code == 200
        body = r.json()
        assert "health" in body

    def test_admins_list_returns_200(self, admin_client, mailbox):
        email = "admin2@admin.test"
        self._login_admin(admin_client, email, mailbox)
        r = admin_client.get("/api/admin/admins")
        assert r.status_code == 200
        assert "admins" in r.json()

    def test_settings_get_returns_200(self, admin_client, mailbox):
        email = "admin3@admin.test"
        self._login_admin(admin_client, email, mailbox)
        r = admin_client.get("/api/admin/settings")
        assert r.status_code == 200
        assert "digest_send_time" in r.json()

    def test_grant_valid_email_returns_ok(self, admin_client, mailbox):
        email = "admin4@admin.test"
        self._login_admin(admin_client, email, mailbox)
        r = admin_client.post("/api/admin/grant", json={"email": "newadmin@example.com"})
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_grant_invalid_email_returns_400(self, admin_client, mailbox):
        email = "admin5@admin.test"
        self._login_admin(admin_client, email, mailbox)
        r = admin_client.post("/api/admin/grant", json={"email": "not-an-email"})
        assert r.status_code == 400

    def test_reindex_status_idle(self, admin_client, mailbox):
        email = "admin6@admin.test"
        self._login_admin(admin_client, email, mailbox)
        r = admin_client.get("/api/admin/reindex-status")
        assert r.status_code == 200
        assert r.json().get("status") == "idle"

    def test_settings_valid_save_returns_ok(self, admin_client, mailbox):
        email = "admin7@admin.test"
        self._login_admin(admin_client, email, mailbox)
        r = admin_client.post("/api/admin/settings", json={"digest_send_time": "08:00"})
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_settings_invalid_time_returns_400(self, admin_client, mailbox):
        email = "admin8@admin.test"
        self._login_admin(admin_client, email, mailbox)
        r = admin_client.post("/api/admin/settings", json={"digest_send_time": "99:99"})
        assert r.status_code == 400

    def test_users_list_returns_200(self, admin_client, mailbox):
        email = "admin9@admin.test"
        self._login_admin(admin_client, email, mailbox)
        r = admin_client.get("/api/admin/users")
        assert r.status_code == 200
        assert "users" in r.json()
        assert isinstance(r.json()["users"], list)

    def test_users_list_has_new_fields(self, admin_client, mailbox):
        """GET /api/admin/users возвращает is_root, is_self, last_login_ip, active_session_count."""
        email = "admin9b@admin.test"
        self._login_admin(admin_client, email, mailbox)
        r = admin_client.get("/api/admin/users")
        assert r.status_code == 200
        users = r.json()["users"]
        assert users, "Список пользователей не должен быть пустым"
        for u in users:
            assert "is_root" in u
            assert "is_self" in u
            assert "active_session_count" in u
            assert "last_login_ip" in u
        # текущий администратор помечён is_self=True
        self_user = next((u for u in users if u["email"] == email), None)
        assert self_user is not None
        assert self_user["is_self"] is True
        # активных сессий у него >= 1
        assert self_user["active_session_count"] >= 1

    def test_users_is_root_flag(self, admin_client, mailbox, monkeypatch):
        """ROOT_ADMIN_EMAIL помечается is_root=True."""
        root_email = "root_flag@admin.test"
        monkeypatch.setattr(admin_router_module, "ROOT_ADMIN_EMAIL", root_email)
        email = "admin9c@admin.test"
        self._login_admin(admin_client, email, mailbox)
        admin_client.post("/api/admin/grant", json={"email": root_email})
        r = admin_client.get("/api/admin/users")
        users = r.json()["users"]
        root_user = next((u for u in users if u["email"] == root_email), None)
        assert root_user is not None
        assert root_user["is_root"] is True

    def test_activate_deactivate_cycle(self, admin_client, mailbox):
        """Цикл: создать пользователя, заблокировать, разблокировать."""
        admin_email = "admin11@admin.test"
        self._login_admin(admin_client, admin_email, mailbox)
        target = "target_user@admin.test"
        # создаём пользователя через grant
        admin_client.post("/api/admin/grant", json={"email": target})

        r = admin_client.post("/api/admin/users/deactivate", json={"email": target})
        assert r.status_code == 200
        assert r.json().get("ok") is True

        r = admin_client.post("/api/admin/users/activate", json={"email": target})
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_users_post_returns_401_without_session(self, admin_client):
        r = admin_client.post("/api/admin/users/activate", json={"email": "x@x.com"})
        assert r.status_code == 401

    def test_deactivate_own_email_returns_403(self, admin_client, mailbox):
        email = "admin13@admin.test"
        self._login_admin(admin_client, email, mailbox)
        r = admin_client.post("/api/admin/users/deactivate", json={"email": email})
        assert r.status_code == 403

    def test_deactivate_root_returns_403(self, admin_client, mailbox, monkeypatch):
        """ROOT_ADMIN_EMAIL нельзя заблокировать даже при наличии прав."""
        root_email = "root@admin.test"
        monkeypatch.setattr(admin_router_module, "ROOT_ADMIN_EMAIL", root_email)
        email = "admin15@admin.test"
        self._login_admin(admin_client, email, mailbox)
        r = admin_client.post("/api/admin/users/deactivate", json={"email": root_email})
        assert r.status_code == 403

    def test_activate_root_succeeds(self, admin_client, mailbox, monkeypatch):
        """ROOT_ADMIN_EMAIL можно разблокировать (activate не ограничена)."""
        root_email = "root@admin.test"
        monkeypatch.setattr(admin_router_module, "ROOT_ADMIN_EMAIL", root_email)
        email = "admin17@admin.test"
        self._login_admin(admin_client, email, mailbox)
        # создаём root-пользователя через grant, затем сразу пробуем активировать
        admin_client.post("/api/admin/grant", json={"email": root_email})
        r = admin_client.post("/api/admin/users/activate", json={"email": root_email})
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_user_logout_401_without_session(self, admin_client):
        """POST /api/admin/users/logout без авторизации → 401."""
        r = admin_client.post("/api/admin/users/logout", json={"email": "x@x.com"})
        assert r.status_code == 401

    def test_user_logout_other_returns_200(self, admin_client, mailbox):
        """Завершить все сессии другого пользователя — 200, сессии удаляются."""
        from services.auth.auth_db import get_all_users as _gau
        admin_email = "admin20@admin.test"
        self._login_admin(admin_client, admin_email, mailbox)

        other = find_or_create_user("logout_other@admin.test", "Other")
        create_session(other["id"], ip="10.0.0.1")
        create_session(other["id"], ip="10.0.0.2")

        r = admin_client.post("/api/admin/users/logout", json={"email": "logout_other@admin.test"})
        assert r.status_code == 200
        assert r.json().get("ok") is True

        # после logout active_session_count == 0
        r2 = admin_client.get("/api/admin/users")
        users = r2.json()["users"]
        other_row = next((u for u in users if u["email"] == "logout_other@admin.test"), None)
        assert other_row is not None
        assert other_row["active_session_count"] == 0

    def test_user_logout_self_preserves_current_session(self, admin_client, mailbox):
        """Logout своей учётки оставляет текущую сессию — остаёмся авторизованными."""
        admin_email = "admin21@admin.test"
        self._login_admin(admin_client, admin_email, mailbox)

        # создаём вторую сессию себе напрямую
        self_user = find_or_create_user(admin_email, "")
        create_session(self_user["id"], ip="9.9.9.9")

        # до logout у себя 2 сессии
        r = admin_client.get("/api/admin/users")
        self_row = next(u for u in r.json()["users"] if u["email"] == admin_email)
        assert self_row["active_session_count"] >= 2

        r2 = admin_client.post("/api/admin/users/logout", json={"email": admin_email})
        assert r2.status_code == 200
        assert r2.json().get("ok") is True

        # после logout текущая сессия по-прежнему валидна
        r3 = admin_client.get("/api/admin/users")
        assert r3.status_code == 200
        self_row2 = next(u for u in r3.json()["users"] if u["email"] == admin_email)
        # только текущая сессия осталась
        assert self_row2["active_session_count"] == 1
