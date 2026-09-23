# Version 1.0 - 23.09.2026 09:30:00 GMT
# Тесты для services/admin/status_service.collect_pcloud_sync()
# Описание: Проверяет статус зеркала data/ в pCloud по файлу-метке: метки нет
#           (зеркало не настроено — не ошибка), метка свежая, метка устарела.

import os
import time

from services.admin.status_service import collect_pcloud_sync

_MODULE = "services.admin.status_service"


def test_no_marker_file_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(f"{_MODULE}.LOG_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(f"{_MODULE}.PCLOUD_SYNC_OK_FILENAME", "pcloud_sync.ok")
    assert collect_pcloud_sync() is None


def test_fresh_marker_is_not_stale(tmp_path, monkeypatch):
    marker = tmp_path / "pcloud_sync.ok"
    marker.touch()
    monkeypatch.setattr(f"{_MODULE}.LOG_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(f"{_MODULE}.PCLOUD_SYNC_OK_FILENAME", "pcloud_sync.ok")
    monkeypatch.setattr(f"{_MODULE}.PCLOUD_SYNC_STALE_HOURS", 2)

    result = collect_pcloud_sync()

    assert result is not None
    assert result["stale"] is False
    assert result["age_minutes"] == 0
    assert result["last_ok"]


def test_old_marker_is_stale(tmp_path, monkeypatch):
    marker = tmp_path / "pcloud_sync.ok"
    marker.touch()
    old_ts = time.time() - 3 * 3600  # 3 часа назад
    os.utime(marker, (old_ts, old_ts))
    monkeypatch.setattr(f"{_MODULE}.LOG_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(f"{_MODULE}.PCLOUD_SYNC_OK_FILENAME", "pcloud_sync.ok")
    monkeypatch.setattr(f"{_MODULE}.PCLOUD_SYNC_STALE_HOURS", 2)

    result = collect_pcloud_sync()

    assert result is not None
    assert result["stale"] is True
    assert result["age_minutes"] >= 179  # ~3 часа, допуск на выполнение теста
