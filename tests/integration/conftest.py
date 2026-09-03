# Version 1.7 - 21.06.2026
# Conftest для integration-тестов TlibWebApp
# Описание: Фикстуры для in-process тестирования auth и upload роутеров без сервера и почты.
# Изменения v1.1: удалён workaround multipart (конфликт устранён через .venv).
# 1.3: мок capture_report_decision — удалён параметр published (сигнатура приведена к новой).
# Изменения v1.2: монкейпатчи перенаправлены на services.upload.upload_service и
#                 services.auth.session_helpers (после выноса бизнес-логики из upload_router).
# Изменения v1.4: autouse-фикстура http_cookie_secure отключает Secure-флаг cookie для
#                 in-process TestClient (http://), после того как AUTH_COOKIE_SECURE стал True по умолчанию.
# Изменения v1.5: autouse-фикстура reset_ip_throttle очищает in-memory словарь пер-IP троттлинга
#                 между тестами, чтобы счётчик не накапливался между запросами.
# Изменения v1.7: Mailbox.capture_admin_alert + заглушка send_admin_alert в upload_router_module
#                 (фикстура mailbox), чтобы disk-guard алерты перехватывались в тестах.

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.auth_router as auth_router_module
import routers.upload_router as upload_router_module
import services.auth.auth_db as auth_db_module
import services.auth.session_helpers as session_helpers_module
import services.auth.request_throttle as request_throttle_module
import services.upload.upload_service as upload_service_module
from routers.auth_router import router as auth_router
from routers.upload_router import router as upload_router
from services.auth.auth_db import find_or_create_user, init_auth_db, set_user_role
from services.database.tlib_table_spec import (
    build_create_table_sql,
    build_insert_sql,
    build_values,
)


# ---------------------------------------------------------------------------
# Mailbox: заглушка для всех email-функций
# ---------------------------------------------------------------------------


class Mailbox:
    """Перехватывает вызовы send_* вместо реальной отправки писем.

    Хранит последний перехваченный magic code и token, а также
    полный список всех вызовов для проверки в тестах.
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.last_code: str | None = None
        self.last_token: str | None = None

    def capture_magic_link(self, email: str, token: str, redirect: str = "", code: str = "") -> None:
        self.calls.append(("magic_link", email, token, redirect, code))
        self.last_code = code
        self.last_token = token

    def capture_new_report(
        self,
        admin_emails: list,
        report_id: str,
        uploader_name: str,
        site_url: str,
        is_edit: bool = False,
    ) -> None:
        self.calls.append(("new_report", admin_emails, report_id, uploader_name, site_url, is_edit))

    def capture_report_decision(
        self,
        uploader_email: str,
        report_id: str,
        admin_comment: str,
        site_url: str,
    ) -> None:
        self.calls.append(("report_decision", uploader_email, report_id, admin_comment, site_url))

    def capture_delete_decision(
        self,
        requester_email: str,
        report_id: str,
        confirmed: bool,
        admin_comment: str,
        site_url: str,
    ) -> None:
        self.calls.append(("delete_decision", requester_email, report_id, confirmed, admin_comment, site_url))

    def capture_delete_request_notice(
        self,
        admin_emails: list,
        report_id: str,
        requester: str,
        site_url: str,
        reason: str = "",
    ) -> None:
        self.calls.append(("delete_request", admin_emails, report_id, requester, site_url, reason))

    def capture_admin_alert(self, event_type: str, **data) -> None:
        """Перехватывает вызовы send_admin_alert в upload_router (disk-guard алерты)."""
        self.calls.append(("admin_alert", event_type, data))

    def last_call_of(self, kind: str) -> tuple | None:
        """Возвращает последний вызов указанного типа или None."""
        for call in reversed(self.calls):
            if call[0] == kind:
                return call
        return None

    def count_of(self, kind: str) -> int:
        return sum(1 for c in self.calls if c[0] == kind)


# ---------------------------------------------------------------------------
# Базовые фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def http_cookie_secure(monkeypatch) -> None:
    """Отключает Secure-флаг cookie для in-process TestClient (работает по http://).

    AUTH_COOKIE_SECURE по умолчанию True (fail-secure для прода), но браузер/TestClient
    не сохраняет Secure-cookie по HTTP, поэтому in-process тесты перестают получать сессию.
    Патчим глобал в модуле роутера: _set_session_cookie и logout читают его во время вызова.
    """
    monkeypatch.setattr(auth_router_module, "AUTH_COOKIE_SECURE", False)


@pytest.fixture(autouse=True)
def reset_ip_throttle():
    """Очищает in-memory словарь пер-IP троттлинга перед каждым тестом.

    TestClient шлёт запросы с одним и тем же IP ('testclient'), поэтому без сброса
    счётчик пер-IP лимита накапливается между тестами и ломает ожидания разных тест-кейсов.
    """
    request_throttle_module._requests.clear()
    yield
    request_throttle_module._requests.clear()


@pytest.fixture()
def mailbox(monkeypatch) -> Mailbox:
    """Заглушает все email-функции в обоих роутерах. ROOT_ADMIN_EMAIL → ""."""
    mb = Mailbox()
    monkeypatch.setattr(auth_router_module, "send_magic_link", mb.capture_magic_link)
    monkeypatch.setattr(upload_service_module, "send_new_report_notice", mb.capture_new_report)
    monkeypatch.setattr(upload_service_module, "send_report_decision", mb.capture_report_decision)
    monkeypatch.setattr(upload_service_module, "send_delete_decision", mb.capture_delete_decision)
    monkeypatch.setattr(upload_service_module, "send_delete_request_notice", mb.capture_delete_request_notice)
    # Перехватываем send_admin_alert в upload_router (disk-guard DISK_LOW алерты)
    monkeypatch.setattr(upload_router_module, "send_admin_alert", mb.capture_admin_alert)
    # Убираем ROOT_ADMIN_EMAIL, чтобы тесты не зависели от env-переменных
    monkeypatch.setattr(auth_router_module, "ROOT_ADMIN_EMAIL", "")
    monkeypatch.setattr(session_helpers_module, "ROOT_ADMIN_EMAIL", "")
    monkeypatch.setattr(upload_service_module, "ROOT_ADMIN_EMAIL", "")
    return mb


@pytest.fixture()
def auth_db_path(tmp_path, monkeypatch) -> str:
    """Создаёт временную auth.db, патчит AUTH_DB_PATH в auth_db_module, инициализирует схему."""
    db_path = str(tmp_path / "auth.db")
    monkeypatch.setattr(auth_db_module, "AUTH_DB_PATH", db_path)
    init_auth_db()
    return db_path


@pytest.fixture()
def tlib_db_path(tmp_path, monkeypatch) -> str:
    """Создаёт временный tlib.db с одной тестовой записью (1-TST), патчит DATABASE_PATH."""
    from config import DATABASE_TABLE_NAME

    db_path = str(tmp_path / "tlib.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(build_create_table_sql(DATABASE_TABLE_NAME))
    conn.execute(
        build_insert_sql(DATABASE_TABLE_NAME),
        build_values({
            "Шифр": 1,
            "ДопШифр": "TST",
            "Маршрут": "Тестовый маршрут",
            "Год": 2024,
            "ЗагрузилID": None,
        }),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(upload_service_module, "DATABASE_PATH", db_path)
    return db_path


@pytest.fixture()
def tmp_dirs(tmp_path, monkeypatch) -> dict[str, Path]:
    """Создаёт временные директории pipeline и патчит их константы в upload_router."""
    dirs: dict[str, Path] = {
        "staging": tmp_path / "10_up",
        "go": tmp_path / "20_go",
        "processing": tmp_path / "30_processing",
        "data": tmp_path / "data",
        "backup": tmp_path / "data.old",
        "notify": tmp_path / "notify",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(upload_service_module, "UPLOAD_STAGING_DIRECTORY", str(dirs["staging"]))
    monkeypatch.setattr(upload_service_module, "UPLOAD_GO_DIRECTORY", str(dirs["go"]))
    monkeypatch.setattr(upload_service_module, "UPLOAD_PROCESSING_DIRECTORY", str(dirs["processing"]))
    monkeypatch.setattr(upload_service_module, "DATA_DIRECTORY", str(dirs["data"]))
    monkeypatch.setattr(upload_service_module, "BACKUP_DIRECTORY", str(dirs["backup"]))
    monkeypatch.setattr(upload_service_module, "PENDING_NOTIFY_DIRECTORY", str(dirs["notify"]))
    return dirs


@pytest.fixture()
def app() -> FastAPI:
    """Минимальное FastAPI-приложение из двух роутеров (без lifespan и middleware)."""
    application = FastAPI()
    application.include_router(auth_router)
    application.include_router(upload_router)
    return application


@pytest.fixture()
def client(app) -> TestClient:
    """TestClient для in-process HTTP-запросов к тестовому приложению."""
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# Хелперы авторизации — fixture factories
# ---------------------------------------------------------------------------


def _do_login(client: TestClient, email: str, mailbox: Mailbox) -> None:
    """Полный login-флоу: request-link → код из mailbox → verify-code → cookie в client."""
    r = client.post("/api/auth/request-link", json={"email": email})
    assert r.status_code == 200, f"request-link вернул {r.status_code}: {r.text}"
    code = mailbox.last_code
    assert code, "Magic code не был перехвачен Mailbox"
    r2 = client.post("/api/auth/verify-code", json={"email": email, "code": code})
    assert r2.status_code == 200, f"verify-code вернул {r2.status_code}: {r2.text}"


@pytest.fixture()
def logged_in_user(client, auth_db_path, mailbox) -> str:
    """Логинит обычного пользователя; возвращает email. Cookie записана в client."""
    email = "user@integration.test"
    _do_login(client, email, mailbox)
    return email


@pytest.fixture()
def logged_in_admin(client, auth_db_path, mailbox) -> str:
    """Логинит пользователя и выдаёт роль admin через auth_db; возвращает email."""
    email = "admin@integration.test"
    _do_login(client, email, mailbox)
    set_user_role(email, "admin")
    return email


