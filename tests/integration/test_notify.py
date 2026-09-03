# Version 1.0 - 12.06.2026 20:00:00 GMT
# Integration tests: services/file_watcher/notify.py
# Описание: Проверяет process_pending_notifications() — обработку .notify-маркеров.
#           Все константы и email-функции мокаются через monkeypatch на модуль notify,
#           так как импортированы на уровне модуля. Файловая система — tmp_path.

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import services.file_watcher.notify as notify_module


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _make_user(email: str = "author@test.com") -> dict:
    return {"id": 42, "email": email, "name": "Author"}


# ---------------------------------------------------------------------------
# Фикстура окружения
# ---------------------------------------------------------------------------


@pytest.fixture()
def notify_env(tmp_path, monkeypatch):
    """
    Создаёт tmp-директории и патчит module-level константы в notify.py.
    Email-функции и DB-вызовы заменяются заглушками.
    """
    dirs = {
        "notify": tmp_path / "notify",
        "data":   tmp_path / "data",
        "error":  tmp_path / "40_error",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(notify_module, "PENDING_NOTIFY_DIRECTORY", str(dirs["notify"]))
    monkeypatch.setattr(notify_module, "DATA_DIRECTORY",            str(dirs["data"]))
    monkeypatch.setattr(notify_module, "UPLOAD_ERROR_DIRECTORY",    str(dirs["error"]))
    monkeypatch.setattr(notify_module, "SITE_URL",                  "https://test.tlib.ru")

    # Заглушки по умолчанию (переопределяются в конкретных тестах)
    monkeypatch.setattr(notify_module, "get_user_by_id",               MagicMock(return_value=None))
    monkeypatch.setattr(notify_module, "collect_admin_emails",          MagicMock(return_value=["admin@test.com"]))
    monkeypatch.setattr(notify_module, "send_report_published",         MagicMock())
    monkeypatch.setattr(notify_module, "send_processing_failed_notice", MagicMock())

    return dirs


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


def test_no_notify_directory_is_noop(tmp_path, monkeypatch):
    """Директории маркеров нет — функция завершается без ошибок."""
    monkeypatch.setattr(notify_module, "PENDING_NOTIFY_DIRECTORY", str(tmp_path / "nonexistent"))
    notify_module.process_pending_notifications()  # не должно бросать


def test_no_markers_is_noop(notify_env):
    """Директория существует, но маркеров нет — no-op."""
    notify_module.process_pending_notifications()
    notify_module.send_report_published.assert_not_called()
    notify_module.send_processing_failed_notice.assert_not_called()


def test_published_with_author_sends_email_and_removes_marker(notify_env, monkeypatch):
    """Маркер + JSON в data/ с ЗагрузилID → письмо автору, маркер удалён."""
    notify_module.get_user_by_id.return_value = _make_user("author@example.com")

    marker = notify_env["notify"] / "00500-TST.notify"
    marker.write_text("Опубликован", encoding="utf-8")
    _write_json(
        notify_env["data"] / "00500-TST.json",
        {"Шифр": 500, "ДопШифр": "TST", "Маршрут": "Маршрут", "ЗагрузилID": 42},
    )

    notify_module.process_pending_notifications()

    assert not marker.exists(), "маркер должен быть удалён"
    notify_module.send_report_published.assert_called_once()
    call_args = notify_module.send_report_published.call_args
    assert call_args[0][0] == "author@example.com"
    assert "00500-TST" in call_args[0][1]


def test_published_without_zagruzil_id_removes_marker_silently(notify_env):
    """JSON без ЗагрузилID → маркер удалён, письма нет."""
    marker = notify_env["notify"] / "00501-TST.notify"
    marker.write_text("", encoding="utf-8")
    _write_json(
        notify_env["data"] / "00501-TST.json",
        {"Шифр": 501, "ДопШифр": "TST", "Маршрут": "Маршрут"},
    )

    notify_module.process_pending_notifications()

    assert not marker.exists()
    notify_module.send_report_published.assert_not_called()


def test_published_user_not_found_removes_marker(notify_env):
    """get_user_by_id возвращает None → маркер удалён, письма нет."""
    notify_module.get_user_by_id.return_value = None

    marker = notify_env["notify"] / "00502-TST.notify"
    marker.write_text("", encoding="utf-8")
    _write_json(
        notify_env["data"] / "00502-TST.json",
        {"Шифр": 502, "ДопШифр": "TST", "Маршрут": "X", "ЗагрузилID": 99},
    )

    notify_module.process_pending_notifications()

    assert not marker.exists()
    notify_module.send_report_published.assert_not_called()


def test_smtp_error_keeps_marker(notify_env):
    """SMTP-ошибка при send_report_published → маркер ОСТАЁТСЯ для повтора."""
    notify_module.get_user_by_id.return_value = _make_user()
    notify_module.send_report_published.side_effect = RuntimeError("SMTP failed")

    marker = notify_env["notify"] / "00503-TST.notify"
    marker.write_text("", encoding="utf-8")
    _write_json(
        notify_env["data"] / "00503-TST.json",
        {"Шифр": 503, "ДопШифр": "TST", "Маршрут": "X", "ЗагрузилID": 42},
    )

    notify_module.process_pending_notifications()

    assert marker.exists(), "маркер должен остаться при SMTP-ошибке"


def test_failed_sends_email_to_admins_and_removes_marker(notify_env):
    """Файлы в 40_error/ → письмо админам с текстом .err, маркер удалён."""
    marker = notify_env["notify"] / "00504-TST.notify"
    marker.write_text("", encoding="utf-8")
    (notify_env["error"] / "00504-TST.json").write_bytes(b"{}")
    (notify_env["error"] / "00504-TST.err").write_text("Ошибка валидации", encoding="utf-8")

    notify_module.process_pending_notifications()

    assert not marker.exists()
    notify_module.send_processing_failed_notice.assert_called_once()
    call_args = notify_module.send_processing_failed_notice.call_args
    assert call_args[0][0] == ["admin@test.com"]
    assert "Ошибка валидации" in call_args[0][2]


def test_failed_no_admins_removes_marker(notify_env):
    """collect_admin_emails пуст → маркер удалён без письма."""
    notify_module.collect_admin_emails.return_value = []

    marker = notify_env["notify"] / "00505-TST.notify"
    marker.write_text("", encoding="utf-8")
    (notify_env["error"] / "00505-TST.json").write_bytes(b"{}")

    notify_module.process_pending_notifications()

    assert not marker.exists()
    notify_module.send_processing_failed_notice.assert_not_called()


def test_in_queue_marker_stays(notify_env):
    """Ни data/ ни error/ — отчёт ещё в очереди, маркер остаётся."""
    marker = notify_env["notify"] / "00506-TST.notify"
    marker.write_text("", encoding="utf-8")

    notify_module.process_pending_notifications()

    assert marker.exists()
    notify_module.send_report_published.assert_not_called()
    notify_module.send_processing_failed_notice.assert_not_called()


class TestBuildReportUrl:
    """Тесты для _build_report_url."""

    def _url(self, report: dict, site_url: str = "https://tlib.ru") -> str:
        import services.file_watcher.notify as nm
        original = nm.SITE_URL
        nm.SITE_URL = site_url
        try:
            return nm._build_report_url(report)
        finally:
            nm.SITE_URL = original

    def test_with_dopshifr(self):
        url = self._url({"Шифр": 1, "ДопШифр": "TST"})
        assert url.endswith("/?1-TST")

    def test_without_dopshifr(self):
        url = self._url({"Шифр": 42})
        assert url.endswith("/?42")

    def test_cyrillic_dopshifr_is_percent_encoded(self):
        url = self._url({"Шифр": 1, "ДопШифр": "ТССР"})
        # %-кодированный результат не должен содержать кириллицу напрямую
        suffix = url.split("/?", 1)[1]
        assert all(ord(c) < 128 for c in suffix)
