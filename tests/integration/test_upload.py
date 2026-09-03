# Version 1.4 - 21.06.2026
# Integration tests: upload endpoints
# Описание: In-process тесты для всех endpoints /api/upload/*.
# 1.1: удалён ассерт call[3] is False (published=False) — параметр убран из send_report_decision.
# 1.2: тест 413 обновлён под потоковую схему (patching upload_router.MAX_ARCHIVE_SIZE);
#      добавлены TestOversize (temp-мусор) и TestDiskGuard (507 + /status).
# 1.3: регресс-тест очистки скопированного архива при сбое записи .editmeta (ветка copy2).
# 1.4: тесты DISK_LOW-алерта при блокировке аплоада (submit, submit-edit, /status, happy-path).
#           Проверяют: контроль доступа (401/403), бизнес-логику (next-code, check-code,
#           submit, publish, reject, submit-edit, request-delete, confirm/reject-delete)
#           и файловые инварианты (файлы появляются в нужных директориях).
#           Почта заглушена; все директории и обе БД — во временном tmp_path.

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import routers.upload_router as upload_router_module
import services.upload.upload_service as upload_service_module
from tests.integration.helpers import set_zagruzil_id, submit_report


# ---------------------------------------------------------------------------
# Контроль доступа: анонимные запросы → 401
# ---------------------------------------------------------------------------


class TestAccessControl:
    """Без cookie все endpoints возвращают 401 (или 403 для неадминов)."""

    def test_next_code_anonymous_returns_401(self, client, auth_db_path, tlib_db_path, tmp_dirs, mailbox):
        r = client.get("/api/upload/next-code")
        assert r.status_code == 401

    def test_check_code_anonymous_returns_401(self, client, auth_db_path, tlib_db_path, tmp_dirs, mailbox):
        r = client.get("/api/upload/check-code?shifr=1&dopshifr=TST")
        assert r.status_code == 401

    def test_submit_anonymous_returns_401(self, client, auth_db_path, tlib_db_path, tmp_dirs, mailbox):
        r = submit_report(client)
        assert r.status_code == 401

    def test_list_anonymous_returns_401(self, client, auth_db_path, tlib_db_path, tmp_dirs, mailbox):
        r = client.get("/api/upload/list")
        assert r.status_code == 401

    def test_publish_anonymous_returns_401(self, client, auth_db_path, tlib_db_path, tmp_dirs, mailbox):
        r = client.post("/api/upload/publish", data={"id": "X", "shifr": 1, "dopshifr": "T", "marshrut": "X", "god": 2024})
        assert r.status_code == 401

    def test_reject_anonymous_returns_401(self, client, auth_db_path, tlib_db_path, tmp_dirs, mailbox):
        r = client.post("/api/upload/reject", json={"id": "X"})
        assert r.status_code == 401

    def test_published_item_anonymous_returns_401(self, client, auth_db_path, tlib_db_path, tmp_dirs, mailbox):
        r = client.get("/api/upload/published-item?id=00001-TST")
        assert r.status_code == 401

    def test_submit_edit_anonymous_returns_401(self, client, auth_db_path, tlib_db_path, tmp_dirs, mailbox):
        r = client.post(
            "/api/upload/submit-edit",
            data={"edit_orig_id": "00001-TST", "shifr": 1, "dopshifr": "TST", "marshrut": "X", "god": 2024},
            files={"file": ("x.zip", b"PK", "application/zip")},
        )
        assert r.status_code == 401

    def test_request_delete_anonymous_returns_401(self, client, auth_db_path, tlib_db_path, tmp_dirs, mailbox):
        r = client.post("/api/upload/request-delete", json={"id": "00001-TST", "confirm_code": "00001-TST", "reason": "test"})
        assert r.status_code == 401

    def test_confirm_delete_anonymous_returns_401(self, client, auth_db_path, tlib_db_path, tmp_dirs, mailbox):
        r = client.post("/api/upload/confirm-delete", json={"id": "00001-TST"})
        assert r.status_code == 401

    def test_list_regular_user_returns_401(self, client, logged_in_user, tlib_db_path, tmp_dirs):
        r = client.get("/api/upload/list")
        assert r.status_code == 401

    def test_publish_regular_user_returns_401(
        self, client, logged_in_user, tlib_db_path, tmp_dirs
    ):
        r = client.post(
            "/api/upload/publish",
            data={"id": "X", "shifr": 1, "dopshifr": "T", "marshrut": "X", "god": 2024},
        )
        assert r.status_code == 401

    def test_published_item_regular_user_non_author_returns_403(
        self, client, logged_in_user, tlib_db_path, tmp_dirs
    ):
        # ЗагрузилID=None → non-admin не может смотреть
        r = client.get("/api/upload/published-item?id=00001-TST")
        assert r.status_code == 403

    def test_submit_edit_regular_user_non_author_returns_403(
        self, client, logged_in_user, tlib_db_path, tmp_dirs
    ):
        r = client.post(
            "/api/upload/submit-edit",
            data={"edit_orig_id": "00001-TST", "shifr": 1, "dopshifr": "TST", "marshrut": "X", "god": 2024},
            files={"file": ("x.zip", b"PK", "application/zip")},
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# next-code
# ---------------------------------------------------------------------------


class TestNextCode:
    def test_empty_db_returns_101(self, client, logged_in_user, tlib_db_path, tmp_dirs):
        r = client.get("/api/upload/next-code")
        assert r.status_code == 200
        data = r.json()
        assert data["shifr"] == 101
        assert data["dopshifr"] == "TLIB"

    def test_considers_existing_tlib_codes_in_db(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs
    ):
        from config import DATABASE_TABLE_NAME
        # Добавляем 101-TLIB и 102-TLIB в tlib.db
        conn = sqlite3.connect(tlib_db_path)
        from services.database.tlib_table_spec import build_insert_sql, build_values
        for n in (101, 102):
            conn.execute(
                build_insert_sql(DATABASE_TABLE_NAME),
                build_values({"Шифр": n, "ДопШифр": "TLIB", "Маршрут": f"Тест {n}", "Год": 2024}),
            )
        conn.commit()
        conn.close()
        r = client.get("/api/upload/next-code")
        assert r.status_code == 200
        assert r.json()["shifr"] == 103

    def test_considers_staging_files(self, client, logged_in_user, tlib_db_path, tmp_dirs):
        # Кладём 101-TLIB.json в 10_up
        staging = tmp_dirs["staging"]
        (staging / "00101-TLIB.json").write_text("{}", encoding="utf-8")
        r = client.get("/api/upload/next-code")
        assert r.status_code == 200
        assert r.json()["shifr"] == 102


# ---------------------------------------------------------------------------
# check-code
# ---------------------------------------------------------------------------


class TestCheckCode:
    def test_free_code_returns_not_taken(self, client, logged_in_user, tlib_db_path, tmp_dirs):
        r = client.get("/api/upload/check-code?shifr=999&dopshifr=FREE")
        assert r.status_code == 200
        data = r.json()
        assert data["taken"] is False
        assert data["normalized"] == "00999-FREE"

    def test_code_in_db_is_taken(self, client, logged_in_user, tlib_db_path, tmp_dirs):
        # tlib.db содержит (1, TST)
        r = client.get("/api/upload/check-code?shifr=1&dopshifr=TST")
        assert r.status_code == 200
        data = r.json()
        assert data["taken"] is True
        assert data["in_library"] is True

    def test_code_in_staging_is_taken(self, client, logged_in_user, tlib_db_path, tmp_dirs):
        (tmp_dirs["staging"] / "00300-STAGE.json").write_text("{}", encoding="utf-8")
        r = client.get("/api/upload/check-code?shifr=300&dopshifr=STAGE")
        assert r.status_code == 200
        assert r.json()["taken"] is True

    def test_normalization_case_insensitive(self, client, logged_in_user, tlib_db_path, tmp_dirs):
        # "1-tst" должен совпасть с "00001-TST" из tlib.db
        r = client.get("/api/upload/check-code?shifr=1&dopshifr=tst")
        assert r.status_code == 200
        assert r.json()["taken"] is True

    def test_exclude_param_frees_own_code(self, client, logged_in_admin, tlib_db_path, tmp_dirs):
        # Код 1-TST занят в БД, но с exclude=00001-TST он должен быть свободен
        r = client.get("/api/upload/check-code?shifr=1&dopshifr=TST&exclude=00001-TST")
        assert r.status_code == 200
        assert r.json()["taken"] is False

    def test_admin_can_edit_library_report(self, client, logged_in_admin, tlib_db_path, tmp_dirs):
        r = client.get("/api/upload/check-code?shifr=1&dopshifr=TST")
        assert r.status_code == 200
        data = r.json()
        assert data["taken"] is True
        assert data["in_library"] is True
        assert data["can_edit"] is True


# ---------------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------------


class TestSubmit:
    def test_valid_submit_creates_files_in_staging(
        self, client, logged_in_user, tlib_db_path, tmp_dirs, mailbox
    ):
        r = submit_report(client, shifr=200, dopshifr="NEW")
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True
        assert data.get("id") == "00200-NEW"
        staging = tmp_dirs["staging"]
        assert (staging / "00200-NEW.json").exists()
        assert (staging / "00200-NEW.zip").exists()

    def test_submit_json_contains_correct_fields(
        self, client, logged_in_user, tlib_db_path, tmp_dirs, mailbox
    ):
        r = submit_report(client, shifr=201, dopshifr="CHK", marshrut="Горный маршрут", god=2025)
        assert r.status_code == 200
        json_path = tmp_dirs["staging"] / "00201-CHK.json"
        saved = json.loads(json_path.read_text(encoding="utf-8"))
        assert saved["Шифр"] == 201
        assert saved["ДопШифр"] == "CHK"
        assert saved["Маршрут"] == "Горный маршрут"
        assert saved["Год"] == 2025
        assert saved["ТипФайла"] == "zip"

    def test_submit_duplicate_returns_409(
        self, client, logged_in_user, tlib_db_path, tmp_dirs, mailbox
    ):
        # Шифр=1, ДопШифр=TST уже занят в tlib.db
        r = submit_report(client, shifr=1, dopshifr="TST")
        assert r.status_code == 409
        assert r.json().get("code_taken") is True

    def test_submit_invalid_extension_returns_400(
        self, client, logged_in_user, tlib_db_path, tmp_dirs, mailbox
    ):
        r = client.post(
            "/api/upload/submit",
            data={"shifr": 202, "dopshifr": "BAD", "marshrut": "X", "god": 2024},
            files={"file": ("file.txt", b"text content", "text/plain")},
        )
        assert r.status_code == 400

    def test_submit_oversized_file_returns_413(
        self, client, logged_in_user, tlib_db_path, tmp_dirs, mailbox, monkeypatch
    ):
        # Patch router (передаёт лимит в stream_upload_to_temp) + сервис (safety-net)
        monkeypatch.setattr(upload_router_module, "MAX_ARCHIVE_SIZE", 10)
        monkeypatch.setattr(upload_service_module, "MAX_ARCHIVE_SIZE", 10)
        r = submit_report(client, shifr=203, dopshifr="BIG", content=b"0" * 20)
        assert r.status_code == 413

    def test_submit_sends_notice_to_admins(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        # При логине как admin — get_admin_users() вернёт его, письмо отправится
        r = submit_report(client, shifr=204, dopshifr="NTFY")
        assert r.status_code == 200
        assert mailbox.count_of("new_report") == 1


# ---------------------------------------------------------------------------
# list / item / file
# ---------------------------------------------------------------------------


class TestListAndItem:
    def test_list_returns_staged_reports(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        # Подготовка: submit отчёта
        submit_report(client, shifr=210, dopshifr="LST")
        r = client.get("/api/upload/list")
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True
        ids = [rep["id"] for rep in data["reports"]]
        assert "00210-LST" in ids

    def test_item_returns_report_data(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        submit_report(client, shifr=211, dopshifr="ITM")
        r = client.get("/api/upload/item?id=00211-ITM")
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True
        assert data["data"]["Шифр"] == 211

    def test_item_not_found_returns_404(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        r = client.get("/api/upload/item?id=99999-NOTEXIST")
        assert r.status_code == 404

    def test_file_download_returns_file(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        submit_report(client, shifr=212, dopshifr="DL", content=b"PK real content")
        r = client.get("/api/upload/file?id=00212-DL")
        assert r.status_code == 200
        assert r.content == b"PK real content"


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------


class TestPublish:
    def _publish(self, client, orig_id: str, shifr: int, dopshifr: str) -> "httpx.Response":
        return client.post(
            "/api/upload/publish",
            data={
                "id": orig_id,
                "shifr": shifr,
                "dopshifr": dopshifr,
                "marshrut": "Горный маршрут",
                "god": 2024,
                "no_email": True,  # не создавать notify-маркер в тестах
            },
        )

    def test_publish_moves_pair_to_20_go(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        submit_report(client, shifr=300, dopshifr="PUB")
        r = self._publish(client, "00300-PUB", 300, "PUB")
        assert r.status_code == 200
        assert r.json().get("ok") is True
        go = tmp_dirs["go"]
        assert (go / "00300-PUB.json").exists()
        assert (go / "00300-PUB.zip").exists()
        # Из staging удалён
        staging = tmp_dirs["staging"]
        assert not (staging / "00300-PUB.json").exists()

    def test_publish_creates_notify_marker(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        submit_report(client, shifr=301, dopshifr="NTF")
        r = client.post(
            "/api/upload/publish",
            data={
                "id": "00301-NTF",
                "shifr": 301,
                "dopshifr": "NTF",
                "marshrut": "Тест",
                "god": 2024,
                "admin_comment": "Опубликовано",
                "no_email": False,
            },
        )
        assert r.status_code == 200
        notify = tmp_dirs["notify"]
        assert (notify / "00301-NTF.notify").exists()

    def test_publish_not_found_returns_404(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        r = self._publish(client, "99999-GHOST", 99999, "GHOST")
        assert r.status_code == 404

    def test_publish_duplicate_code_returns_409(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        # Подготовка: submit двух отчётов
        submit_report(client, shifr=302, dopshifr="A")
        submit_report(client, shifr=303, dopshifr="B")
        # Публикуем 302-A, потом пробуем опубликовать 303-B как 302-A (занятый код)
        self._publish(client, "00302-A", 302, "A")  # первый публикуем успешно
        r = client.post(
            "/api/upload/publish",
            data={"id": "00303-B", "shifr": 302, "dopshifr": "A", "marshrut": "X", "god": 2024},
        )
        assert r.status_code == 409


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------


class TestReject:
    def test_reject_moves_files_to_backup(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        submit_report(client, shifr=400, dopshifr="REJ")
        r = client.post(
            "/api/upload/reject",
            json={"id": "00400-REJ", "admin_comment": "Не соответствует требованиям", "no_email": True},
        )
        assert r.status_code == 200
        assert r.json().get("ok") is True
        backup = tmp_dirs["backup"]
        backup_files = list(backup.glob("00400-REJ_*.json"))
        assert len(backup_files) == 1
        err_files = list(backup.glob("00400-REJ_*.err"))
        assert len(err_files) == 1

    def test_reject_err_contains_comment(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        submit_report(client, shifr=401, dopshifr="ERR")
        client.post(
            "/api/upload/reject",
            json={"id": "00401-ERR", "admin_comment": "Уникальный комментарий 12345", "no_email": True},
        )
        backup = tmp_dirs["backup"]
        err_file = next(backup.glob("00401-ERR_*.err"))
        content = err_file.read_text(encoding="utf-8")
        assert "Уникальный комментарий 12345" in content

    def test_reject_not_found_returns_404(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        r = client.post("/api/upload/reject", json={"id": "NOEXIST-X"})
        assert r.status_code == 404

    def test_reject_sends_email_when_zagruzil_id_set(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        # Создаём пользователя и подаём отчёт
        from services.auth import auth_db as adb

        uploader_email = "uploader@example.com"
        adb.find_or_create_user(uploader_email, "Uploader")
        uploader = adb.get_user_by_email(uploader_email)
        submit_report(client, shifr=402, dopshifr="MAIL")

        # Патчим ЗагрузилID в JSON-файле напрямую (upload записывает admin-пользователя)
        staging = tmp_dirs["staging"]
        json_path = staging / "00402-MAIL.json"
        data = json.loads(json_path.read_text(encoding="utf-8"))
        data["ЗагрузилID"] = uploader["id"]
        json_path.write_text(json.dumps(data), encoding="utf-8")

        mailbox.calls.clear()
        client.post(
            "/api/upload/reject",
            json={"id": "00402-MAIL", "admin_comment": "", "no_email": False},
        )
        assert mailbox.count_of("report_decision") == 1
        call = mailbox.last_call_of("report_decision")
        assert call[1] == uploader_email


# ---------------------------------------------------------------------------
# submit-edit (правка опубликованного отчёта)
# ---------------------------------------------------------------------------


class TestSubmitEdit:
    def test_admin_can_edit_any_report(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        r = client.post(
            "/api/upload/submit-edit",
            data={
                "edit_orig_id": "00001-TST",
                "shifr": 1,
                "dopshifr": "TST",
                "marshrut": "Изменённый маршрут",
                "god": 2025,
            },
            files={"file": ("00001-TST.zip", b"PK edited content", "application/zip")},
        )
        assert r.status_code == 200
        assert r.json().get("ok") is True
        # JSON и файл появились в 10_up
        staging = tmp_dirs["staging"]
        assert (staging / "00001-TST.json").exists()
        assert (staging / "00001-TST.zip").exists()
        # .editmeta создан
        assert (staging / "00001-TST.editmeta").exists()

    def test_editmeta_contains_orig_id(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        client.post(
            "/api/upload/submit-edit",
            data={
                "edit_orig_id": "00001-TST",
                "shifr": 500,
                "dopshifr": "NEW",
                "marshrut": "Тест",
                "god": 2024,
            },
            files={"file": ("test.zip", b"PK", "application/zip")},
        )
        editmeta = json.loads(
            (tmp_dirs["staging"] / "00500-NEW.editmeta").read_text(encoding="utf-8")
        )
        assert editmeta["orig_id"] == "00001-TST"

    def test_non_author_cannot_edit(
        self, client, logged_in_user, tlib_db_path, tmp_dirs, mailbox
    ):
        # ЗагрузилID=99 — другой пользователь
        set_zagruzil_id(tlib_db_path, 1, "TST", 99)
        r = client.post(
            "/api/upload/submit-edit",
            data={
                "edit_orig_id": "00001-TST",
                "shifr": 1,
                "dopshifr": "TST",
                "marshrut": "Попытка правки",
                "god": 2024,
            },
            files={"file": ("test.zip", b"PK", "application/zip")},
        )
        assert r.status_code == 403

    def test_author_can_edit_own_report(
        self, client, logged_in_user, tlib_db_path, tmp_dirs, mailbox
    ):
        from services.auth import auth_db as adb

        user = adb.get_user_by_email(logged_in_user)
        set_zagruzil_id(tlib_db_path, 1, "TST", user["id"])
        r = client.post(
            "/api/upload/submit-edit",
            data={
                "edit_orig_id": "00001-TST",
                "shifr": 1,
                "dopshifr": "TST",
                "marshrut": "Правка от автора",
                "god": 2024,
            },
            files={"file": ("test.zip", b"PK", "application/zip")},
        )
        assert r.status_code == 200
        assert r.json().get("ok") is True


# ---------------------------------------------------------------------------
# publish с .editmeta — создание .delete-триггера при смене кода
# ---------------------------------------------------------------------------


class TestPublishWithEditmeta:
    def test_publish_edit_with_code_change_creates_delete_trigger(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        # submit-edit: оригинальный отчёт 00001-TST → новый код 00600-RENM (ДопШифр ≤5 символов)
        r_edit = client.post(
            "/api/upload/submit-edit",
            data={
                "edit_orig_id": "00001-TST",
                "shifr": 600,
                "dopshifr": "RENM",
                "marshrut": "Переименованный",
                "god": 2024,
            },
            files={"file": ("test.zip", b"PK edit", "application/zip")},
        )
        assert r_edit.status_code == 200, r_edit.text
        # Публикуем правку
        r_pub = client.post(
            "/api/upload/publish",
            data={
                "id": "00600-RENM",
                "shifr": 600,
                "dopshifr": "RENM",
                "marshrut": "Переименованный",
                "god": 2024,
                "no_email": True,
            },
        )
        assert r_pub.status_code == 200, r_pub.text
        go = tmp_dirs["go"]
        # .delete-триггер для старого ID должен быть в 20_go
        assert (go / "00001-TST.delete").exists()


# ---------------------------------------------------------------------------
# request-delete / confirm-delete / reject-delete
# ---------------------------------------------------------------------------


class TestDeleteFlow:
    def _ensure_in_library(self, tlib_db_path: str, tmp_dirs: dict) -> None:
        """Создаём .json файл в data/, чтобы _is_in_library нашёл отчёт."""
        (tmp_dirs["data"] / "00001-TST.json").write_text(
            json.dumps({"Шифр": 1, "ДопШифр": "TST"}), encoding="utf-8"
        )

    def test_request_delete_creates_delreq(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        self._ensure_in_library(tlib_db_path, tmp_dirs)
        r = client.post(
            "/api/upload/request-delete",
            json={"id": "00001-TST", "confirm_code": "00001-TST", "reason": "Устаревший отчёт"},
        )
        assert r.status_code == 200
        assert r.json().get("ok") is True
        delreq_path = tmp_dirs["staging"] / "00001-TST.delreq"
        assert delreq_path.exists()
        content = json.loads(delreq_path.read_text(encoding="utf-8"))
        assert content["reason"] == "Устаревший отчёт"

    def test_request_delete_wrong_confirm_code_returns_409(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        self._ensure_in_library(tlib_db_path, tmp_dirs)
        r = client.post(
            "/api/upload/request-delete",
            json={"id": "00001-TST", "confirm_code": "WRONG", "reason": "Тест"},
        )
        assert r.status_code == 409
        assert r.json().get("code_mismatch") is True

    def test_request_delete_loose_code_matches(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        # "1-TST" должен совпасть с "00001-TST" через _loose_code
        self._ensure_in_library(tlib_db_path, tmp_dirs)
        r = client.post(
            "/api/upload/request-delete",
            json={"id": "00001-TST", "confirm_code": "1-TST", "reason": "Тест"},
        )
        assert r.status_code == 200

    def test_request_delete_missing_reason_returns_400(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        self._ensure_in_library(tlib_db_path, tmp_dirs)
        r = client.post(
            "/api/upload/request-delete",
            json={"id": "00001-TST", "confirm_code": "00001-TST", "reason": ""},
        )
        assert r.status_code == 400

    def test_confirm_delete_creates_delete_trigger(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        self._ensure_in_library(tlib_db_path, tmp_dirs)
        # Сначала создаём delreq
        client.post(
            "/api/upload/request-delete",
            json={"id": "00001-TST", "confirm_code": "00001-TST", "reason": "Тест"},
        )
        # Подтверждаем удаление
        r = client.post(
            "/api/upload/confirm-delete",
            json={"id": "00001-TST", "admin_comment": "", "no_email": True},
        )
        assert r.status_code == 200
        assert r.json().get("ok") is True
        # .delete-триггер создан в 20_go
        assert (tmp_dirs["go"] / "00001-TST.delete").exists()
        # .delreq удалён из staging
        assert not (tmp_dirs["staging"] / "00001-TST.delreq").exists()

    def test_reject_delete_removes_delreq(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        self._ensure_in_library(tlib_db_path, tmp_dirs)
        client.post(
            "/api/upload/request-delete",
            json={"id": "00001-TST", "confirm_code": "00001-TST", "reason": "Тест"},
        )
        r = client.post(
            "/api/upload/reject-delete",
            json={"id": "00001-TST", "admin_comment": "Нет оснований", "no_email": True},
        )
        assert r.status_code == 200
        assert r.json().get("ok") is True
        assert not (tmp_dirs["staging"] / "00001-TST.delreq").exists()

    def test_reject_delete_not_found_returns_404(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        r = client.post(
            "/api/upload/reject-delete",
            json={"id": "NODELREQ-X"},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# published-item
# ---------------------------------------------------------------------------


class TestPublishedItem:
    def test_admin_can_access_any_report(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        r = client.get("/api/upload/published-item?id=00001-TST")
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True
        assert data["data"]["Шифр"] == 1
        assert "zagruzil_id" not in data

    def test_author_can_access_own_report(
        self, client, logged_in_user, tlib_db_path, tmp_dirs, mailbox
    ):
        from services.auth import auth_db as adb

        user = adb.get_user_by_email(logged_in_user)
        set_zagruzil_id(tlib_db_path, 1, "TST", user["id"])
        r = client.get("/api/upload/published-item?id=00001-TST")
        assert r.status_code == 200

    def test_not_found_returns_404(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        r = client.get("/api/upload/published-item?id=99999-NOTHERE")
        assert r.status_code == 404

    def test_pending_delete_flag_shown(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox
    ):
        # Создаём .delreq в staging
        (tmp_dirs["staging"] / "00001-TST.delreq").write_text(
            json.dumps({"reason": "удалить", "requested_by_email": "x@y.com", "requested_at": "2024-01-01"}),
            encoding="utf-8",
        )
        r = client.get("/api/upload/published-item?id=00001-TST")
        assert r.status_code == 200
        data = r.json()
        assert data.get("pending_delete") is True


# ---------------------------------------------------------------------------
# lookup-user
# ---------------------------------------------------------------------------


class TestLookupUser:
    def test_lookup_existing_user_returns_data(
        self, client, logged_in_admin, auth_db_path, tlib_db_path, tmp_dirs
    ):
        from services.auth import auth_db as adb
        adb.find_or_create_user("lookup@example.com", "LookuP User")
        r = client.get("/api/upload/lookup-user?email=lookup@example.com")
        assert r.status_code == 200
        data = r.json()
        # lookup-user возвращает found=True и email при успехе
        assert data.get("found") is True or data.get("ok") is True
        assert data.get("email") is not None or data.get("user") is not None

    def test_lookup_nonexistent_user_returns_404_or_not_found(
        self, client, logged_in_admin, auth_db_path, tlib_db_path, tmp_dirs
    ):
        r = client.get("/api/upload/lookup-user?email=nonexistent@example.com")
        # Допускается 404 (пользователь не найден) или 200 с user=null
        assert r.status_code in (200, 404)
        if r.status_code == 200:
            body = r.json()
            assert body.get("user") is None or body.get("ok") is False

    def test_lookup_returns_401_for_anonymous(
        self, client, tlib_db_path, tmp_dirs
    ):
        r = client.get("/api/upload/lookup-user?email=test@example.com")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Потоковая загрузка: oversized file — нет мусора в staging
# ---------------------------------------------------------------------------


class TestOversize:
    def test_oversized_leaves_no_temp_or_staged_files(
        self, client, logged_in_user, tlib_db_path, tmp_dirs, mailbox, monkeypatch
    ):
        """При 413 в staging не должно остаться ни JSON, ни архива, ни _tmp_upload_*."""
        monkeypatch.setattr(upload_router_module, "MAX_ARCHIVE_SIZE", 10)
        monkeypatch.setattr(upload_service_module, "MAX_ARCHIVE_SIZE", 10)
        r = submit_report(client, shifr=600, dopshifr="BIG", content=b"0" * 20)
        assert r.status_code == 413
        staging = tmp_dirs["staging"]
        assert not list(staging.glob("00600-BIG.*"))
        assert not list(staging.glob("_tmp_upload_*"))

    def test_valid_submit_no_temp_files_left(
        self, client, logged_in_user, tlib_db_path, tmp_dirs, mailbox
    ):
        """Успешная загрузка: temp-файл переименован в архив, мусора нет."""
        r = submit_report(client, shifr=601, dopshifr="OK")
        assert r.status_code == 200
        staging = tmp_dirs["staging"]
        assert (staging / "00601-OK.zip").exists()
        assert not list(staging.glob("_tmp_upload_*"))


# ---------------------------------------------------------------------------
# Disk guard: 507 и /api/upload/status
# ---------------------------------------------------------------------------


class TestDiskGuard:
    def test_submit_blocked_when_disk_full_returns_507(
        self, client, logged_in_user, tlib_db_path, tmp_dirs, mailbox, monkeypatch
    ):
        monkeypatch.setattr(
            upload_router_module,
            "disk_allows_upload",
            lambda path: (False, {"reason": "disk_critical", "used_pct": 95, "free_gb": 0.5}),
        )
        r = submit_report(client, shifr=700, dopshifr="DISK")
        assert r.status_code == 507
        assert "место" in r.json().get("error", "").lower() or \
               "недоступ" in r.json().get("error", "").lower()

    def test_submit_edit_blocked_when_disk_full_returns_507(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox, monkeypatch
    ):
        monkeypatch.setattr(
            upload_router_module,
            "disk_allows_upload",
            lambda path: (False, {"reason": "disk_reserve", "used_pct": 80, "free_gb": 1.0}),
        )
        r = client.post(
            "/api/upload/submit-edit",
            data={
                "edit_orig_id": "00001-TST",
                "shifr": 1,
                "dopshifr": "TST",
                "marshrut": "Правка диск",
                "god": 2024,
            },
            files={"file": ("test.zip", b"PK", "application/zip")},
        )
        assert r.status_code == 507

    def test_publish_not_blocked_by_disk_guard(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox, monkeypatch
    ):
        """Публикация admin-пути не проходит через disk-guard."""
        monkeypatch.setattr(
            upload_router_module,
            "disk_allows_upload",
            lambda path: (False, {"reason": "disk_critical", "used_pct": 95, "free_gb": 0.0}),
        )
        # submit даст 507 (disk_allows_upload вызывается), но нам нужно submit_report без guard
        # Проверяем publish напрямую — должен вернуть не 507 (а 404, т.к. отчёта нет)
        r = client.post(
            "/api/upload/publish",
            data={"id": "00999-GHOST", "shifr": 999, "dopshifr": "GHOST", "marshrut": "X", "god": 2024},
        )
        assert r.status_code != 507

    def test_status_returns_uploads_enabled_true(
        self, client, logged_in_user, tlib_db_path, tmp_dirs, monkeypatch
    ):
        monkeypatch.setattr(
            upload_router_module,
            "disk_allows_upload",
            lambda path: (True, {"reason": None, "used_pct": 50, "free_gb": 100.0}),
        )
        r = client.get("/api/upload/status")
        assert r.status_code == 200
        data = r.json()
        assert data["uploads_enabled"] is True
        assert data["reason"] is None

    def test_status_returns_uploads_enabled_false_when_disk_full(
        self, client, logged_in_user, tlib_db_path, tmp_dirs, monkeypatch
    ):
        monkeypatch.setattr(
            upload_router_module,
            "disk_allows_upload",
            lambda path: (False, {"reason": "disk_critical", "used_pct": 95, "free_gb": 0.5}),
        )
        r = client.get("/api/upload/status")
        assert r.status_code == 200
        data = r.json()
        assert data["uploads_enabled"] is False
        assert data["reason"] == "disk_critical"

    def test_status_anonymous_returns_401(self, client, tlib_db_path, tmp_dirs):
        r = client.get("/api/upload/status")
        assert r.status_code == 401

    def test_submit_block_sends_disk_low_alert(
        self, client, logged_in_user, tlib_db_path, tmp_dirs, mailbox, monkeypatch
    ):
        """При блокировке submit disk-guard шлёт DISK_LOW-алерт ровно один раз."""
        monkeypatch.setattr(
            upload_router_module,
            "disk_allows_upload",
            lambda path: (False, {"reason": "disk_critical", "used_pct": 95, "free_gb": 0.5}),
        )
        r = submit_report(client, shifr=800, dopshifr="ALT")
        assert r.status_code == 507
        assert mailbox.count_of("admin_alert") == 1
        call = mailbox.last_call_of("admin_alert")
        assert call[1] == "DISK_LOW"
        assert call[2].get("reason") == "disk_critical"
        assert call[2].get("source") == "upload:submit"

    def test_submit_edit_block_sends_disk_low_alert(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox, monkeypatch
    ):
        """При блокировке submit-edit disk-guard шлёт DISK_LOW-алерт с source=submit-edit."""
        monkeypatch.setattr(
            upload_router_module,
            "disk_allows_upload",
            lambda path: (False, {"reason": "disk_reserve", "used_pct": 80, "free_gb": 1.0}),
        )
        r = client.post(
            "/api/upload/submit-edit",
            data={
                "edit_orig_id": "00001-TST",
                "shifr": 1,
                "dopshifr": "TST",
                "marshrut": "Алерт-тест правки",
                "god": 2024,
            },
            files={"file": ("x.zip", b"PK", "application/zip")},
        )
        assert r.status_code == 507
        assert mailbox.count_of("admin_alert") == 1
        call = mailbox.last_call_of("admin_alert")
        assert call[1] == "DISK_LOW"
        assert call[2].get("reason") == "disk_reserve"
        assert call[2].get("source") == "upload:submit-edit"

    def test_status_does_not_send_alert_when_disk_full(
        self, client, logged_in_user, tlib_db_path, tmp_dirs, mailbox, monkeypatch
    ):
        """/api/upload/status при заполненном диске возвращает uploads_enabled=false,
        но НЕ шлёт алерт (read-only, может опрашиваться часто)."""
        monkeypatch.setattr(
            upload_router_module,
            "disk_allows_upload",
            lambda path: (False, {"reason": "disk_critical", "used_pct": 95, "free_gb": 0.5}),
        )
        r = client.get("/api/upload/status")
        assert r.status_code == 200
        assert r.json()["uploads_enabled"] is False
        assert mailbox.count_of("admin_alert") == 0

    def test_happy_submit_no_disk_alert(
        self, client, logged_in_user, tlib_db_path, tmp_dirs, mailbox, monkeypatch
    ):
        """Успешный submit не порождает DISK_LOW-алерт."""
        monkeypatch.setattr(
            upload_router_module,
            "disk_allows_upload",
            lambda path: (True, {"reason": None, "used_pct": 50, "free_gb": 100.0}),
        )
        r = submit_report(client, shifr=801, dopshifr="OK")
        assert r.status_code == 200
        assert mailbox.count_of("admin_alert") == 0


# ---------------------------------------------------------------------------
# Регресс: очистка скопированного архива при сбое записи .editmeta (ветка copy2)
# ---------------------------------------------------------------------------


class TestSubmitEditCleanup:
    def test_copy2_branch_cleanup_on_editmeta_failure(
        self, client, logged_in_admin, tlib_db_path, tmp_dirs, mailbox, monkeypatch
    ):
        """При сбое записи .editmeta в ветке copy2 (правка без нового файла, смена ID)
        скопированный архив должен быть удалён из staging, а не остаться сиротой.

        Регресс против бага: 'if archive_path_new and has_new_file' пропускало очистку
        при has_new_file=False (ветка shutil.copy2). После фикса — 'if archive_path_new:'
        удаляет файл независимо от того, откуда он взялся.
        """
        # Подготовка: создаём архив опубликованного отчёта в data/, чтобы
        # find_published_pair нашла его и выбрала ветку copy2.
        data_dir = tmp_dirs["data"]
        src_zip = data_dir / "00001-TST.zip"
        src_zip.write_bytes(b"PK src archive")

        staging = tmp_dirs["staging"]

        # Инжектируем сбой: write_text бросает исключение только при записи .editmeta,
        # остальные вызовы (JSON) проходят через оригинальный метод.
        original_write_text = Path.write_text

        def _failing_write_text(self, data, **kwargs):
            if self.name.endswith(".editmeta"):
                raise OSError("injected editmeta write failure")
            return original_write_text(self, data, **kwargs)

        monkeypatch.setattr(Path, "write_text", _failing_write_text)

        # submit-edit без нового файла, со сменой ID (00001-TST -> 00700-NEW):
        # сервис скопирует 00001-TST.zip -> 10_up/00700-NEW.zip (shutil.copy2),
        # затем попытается записать .editmeta — провалится.
        r = client.post(
            "/api/upload/submit-edit",
            data={
                "edit_orig_id": "00001-TST",
                "shifr": 700,
                "dopshifr": "NEW",
                "marshrut": "Правка со сменой кода",
                "god": 2024,
            },
            # файл не прикладываем → ветка copy2
        )

        assert r.status_code == 500

        # Скопированный архив должен быть очищен (не стать сиротой)
        assert not (staging / "00700-NEW.zip").exists(), (
            "Скопированный архив 00700-NEW.zip не должен оставаться в staging при сбое"
        )
        # JSON тоже должен быть очищен
        assert not (staging / "00700-NEW.json").exists()
        # editmeta — её запись провалилась, файла нет
        assert not (staging / "00700-NEW.editmeta").exists()
