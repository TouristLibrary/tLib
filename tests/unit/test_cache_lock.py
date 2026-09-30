# Version 1.1 - 30.09.2026 16:20:00 GMT
# Тесты lock подготовки кеша (services/cache/cache_prepare_service._acquire_lock)
# Описание: Осиротевший lock — lockdir без _prepare.json старше CACHE_STALE_LOCK_TIMEOUT_MINUTES —
#           снимается и захватывается заново; свежий lock и lock живой подготовки (_prepare.json
#           есть) остаются занятыми. Без этого сирота после рестарта вечно блокировал бы
#           подготовку и докрутку PDF архива.
# 1.1: lock, снятый владельцем между попыткой захвата и проверкой возраста, берётся сразу без WARNING.

import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone

import pytest

import services.cache.cache_prepare_service as prepare_service
from config import CACHE_LOCK_DIRNAME, CACHE_PREPARE_STATUS_FILENAME, CACHE_STALE_LOCK_TIMEOUT_MINUTES

ARCHIVE = "00001-TST"
OLD_INFO = "pid: 1\n"


@pytest.fixture()
def archive_dir(tmp_path, monkeypatch):
    """Папка кеша архива в tmp_path с уже занятым lock (info.txt прежнего владельца)."""
    monkeypatch.setattr(prepare_service, "get_cache_dir", lambda name: tmp_path / name)
    lock_dir = tmp_path / ARCHIVE / CACHE_LOCK_DIRNAME
    lock_dir.mkdir(parents=True)
    (lock_dir / "info.txt").write_text(OLD_INFO, encoding="utf-8")
    return tmp_path / ARCHIVE


def _age_lock(archive_dir):
    """Сдвигает mtime lockdir за таймаут устаревания."""
    old = time.time() - CACHE_STALE_LOCK_TIMEOUT_MINUTES * 60 - 60
    os.utime(archive_dir / CACHE_LOCK_DIRNAME, (old, old))


def test_fresh_lock_without_status_stays_busy(archive_dir):
    # Подготовка могла только что взять lock и ещё не записать _prepare.json
    assert prepare_service._acquire_lock(ARCHIVE) is False
    assert (archive_dir / CACHE_LOCK_DIRNAME / "info.txt").read_text(encoding="utf-8") == OLD_INFO


def test_orphaned_lock_is_removed_and_reacquired(archive_dir):
    _age_lock(archive_dir)

    assert prepare_service._acquire_lock(ARCHIVE) is True

    info = (archive_dir / CACHE_LOCK_DIRNAME / "info.txt").read_text(encoding="utf-8")
    assert info != OLD_INFO
    assert f"pid: {os.getpid()}" in info


def test_lock_released_before_age_check_is_acquired_quietly(archive_dir, monkeypatch, caplog):
    # Гонка: владелец снял lock между неудачным mkdir и stat — захват удаётся сразу, без WARNING
    lock_dir = archive_dir / CACHE_LOCK_DIRNAME
    real_make = prepare_service._make_lock_dir
    calls = []

    def make_then_release(path):
        calls.append(path)
        if len(calls) == 1:
            ok = real_make(path)   # False: lock ещё занят
            shutil.rmtree(lock_dir)  # владелец снял lock до проверки возраста
            return ok
        return real_make(path)

    monkeypatch.setattr(prepare_service, "_make_lock_dir", make_then_release)
    caplog.set_level(logging.WARNING, logger="tlibwebapp")

    assert prepare_service._acquire_lock(ARCHIVE) is True
    assert len(calls) == 2
    assert f"pid: {os.getpid()}" in (lock_dir / "info.txt").read_text(encoding="utf-8")
    assert caplog.records == []


def test_old_lock_with_status_stays_busy(archive_dir):
    # Живая подготовка держит lock дольше таймаута, но пишет _prepare.json — это не сирота
    _age_lock(archive_dir)
    prepare = {"status": "preparing", "stage": "converting",
               "updated_at": datetime.now(timezone.utc).isoformat()}
    (archive_dir / CACHE_PREPARE_STATUS_FILENAME).write_text(json.dumps(prepare), encoding="utf-8")

    assert prepare_service._acquire_lock(ARCHIVE) is False
    assert (archive_dir / CACHE_LOCK_DIRNAME / "info.txt").read_text(encoding="utf-8") == OLD_INFO
