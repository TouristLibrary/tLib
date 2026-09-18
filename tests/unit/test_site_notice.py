# Version 1.0 - 18.09.2026 07:50:00 GMT
# Тесты для services/site_notice.py
# Описание: Проверяет чтение серверного объявления: нет файла, пустой text,
#           валидные text+href, отбрасывание javascript:, слишком длинный text, битый JSON.

import json

from services.site_notice import load_site_notice

_MODULE = "services.site_notice"


def _write_notice(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(f"{_MODULE}.SITE_NOTICE_PATH", str(tmp_path / "missing.json"))
    assert load_site_notice() == {"text": "", "href": ""}


def test_empty_text_returns_empty(tmp_path, monkeypatch):
    notice = tmp_path / "site_notice.json"
    _write_notice(notice, {"text": "   ", "href": "https://example.com"})
    monkeypatch.setattr(f"{_MODULE}.SITE_NOTICE_PATH", str(notice))
    assert load_site_notice() == {"text": "", "href": ""}


def test_text_only_without_href(tmp_path, monkeypatch):
    notice = tmp_path / "site_notice.json"
    _write_notice(notice, {"text": "Краткое объявление"})
    monkeypatch.setattr(f"{_MODULE}.SITE_NOTICE_PATH", str(notice))
    assert load_site_notice() == {"text": "Краткое объявление", "href": ""}


def test_valid_text_and_href(tmp_path, monkeypatch):
    notice = tmp_path / "site_notice.json"
    _write_notice(notice, {
        "text": "  Пример объявления  ",
        "href": "https://example.com/path",
    })
    monkeypatch.setattr(f"{_MODULE}.SITE_NOTICE_PATH", str(notice))
    assert load_site_notice() == {
        "text": "Пример объявления",
        "href": "https://example.com/path",
    }


def test_javascript_href_is_dropped(tmp_path, monkeypatch):
    notice = tmp_path / "site_notice.json"
    _write_notice(notice, {
        "text": "Пример объявления",
        "href": "javascript:alert(1)",
    })
    monkeypatch.setattr(f"{_MODULE}.SITE_NOTICE_PATH", str(notice))
    assert load_site_notice() == {"text": "Пример объявления", "href": ""}


def test_too_long_text_is_hidden(tmp_path, monkeypatch):
    notice = tmp_path / "site_notice.json"
    _write_notice(notice, {"text": "x" * 301, "href": "https://example.com"})
    monkeypatch.setattr(f"{_MODULE}.SITE_NOTICE_PATH", str(notice))
    assert load_site_notice() == {"text": "", "href": ""}


def test_broken_json_returns_empty(tmp_path, monkeypatch):
    notice = tmp_path / "site_notice.json"
    notice.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(f"{_MODULE}.SITE_NOTICE_PATH", str(notice))
    assert load_site_notice() == {"text": "", "href": ""}
