# Version 1.0 - 12.06.2026 18:00:00 GMT
# Unit tests for services/database/update_service.py
# Описание: Проверяет validate_sqlite_database (магический заголовок, невалидные файлы)
#           и perform_database_update (атомарная замена, бэкап, удаление триггера).
#           Всё на tmp-файлах, без живой БД приложения.

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# validate_sqlite_database
# ---------------------------------------------------------------------------


def _make_sqlite(path: Path, table: str = "test") -> Path:
    """Создаёт минимальную валидную SQLite БД."""
    conn = sqlite3.connect(str(path))
    conn.execute(f"CREATE TABLE {table} (id INTEGER)")
    conn.commit()
    conn.close()
    return path


class TestValidateSqliteDatabase:
    def test_valid_sqlite_returns_true(self, tmp_path):
        from services.database.update_service import validate_sqlite_database
        db = _make_sqlite(tmp_path / "valid.db")
        assert validate_sqlite_database(db) is True

    def test_nonexistent_file_returns_false(self, tmp_path):
        from services.database.update_service import validate_sqlite_database
        assert validate_sqlite_database(tmp_path / "ghost.db") is False

    def test_text_file_returns_false(self, tmp_path):
        from services.database.update_service import validate_sqlite_database
        bad = tmp_path / "bad.db"
        bad.write_text("not a sqlite database", encoding="utf-8")
        assert validate_sqlite_database(bad) is False

    def test_empty_file_returns_false(self, tmp_path):
        from services.database.update_service import validate_sqlite_database
        empty = tmp_path / "empty.db"
        empty.write_bytes(b"")
        assert validate_sqlite_database(empty) is False

    def test_truncated_header_returns_false(self, tmp_path):
        from services.database.update_service import validate_sqlite_database
        truncated = tmp_path / "truncated.db"
        truncated.write_bytes(b"SQLite format")  # заголовок обрезан
        assert validate_sqlite_database(truncated) is False


# ---------------------------------------------------------------------------
# perform_database_update
# ---------------------------------------------------------------------------


def _dummy_state():
    return SimpleNamespace(
        dopshifr_list=[],
        raion_obshiy_list=[],
        tip_list=[],
        kategoria_s_list=[],
        kategoria_po_list=[],
        kategoria_unified_list=[],
        reports_count=0,
        reference_version=None,
        redirect_table={},
    )


class TestPerformDatabaseUpdate:
    def _run(self, tmp_path: Path):
        from services.database.update_service import perform_database_update
        from config import DATABASE_BACKUP_PREFIX, BACKUP_TIMESTAMP_FORMAT

        db_dir = tmp_path / "db_dir"
        db_dir.mkdir()
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()

        current_db = db_dir / "tlib.db"
        _make_sqlite(current_db)

        new_db = db_dir / "tlib-new.db"
        _make_sqlite(new_db)

        app_state = _dummy_state()

        with (
            patch("services.database.update_service.BACKUP_DIRECTORY", str(backup_dir)),
            patch("services.database.update_service.load_reference_lists", return_value={
                "dopshifr_list": [],
                "raion_obshiy_list": [],
                "tip_list": [],
                "kategoria_s_list": [],
                "kategoria_po_list": [],
                "kategoria_unified_list": [],
                "reports_count": 42,
                "redirect_table": {},
            }),
            patch("services.database.update_service.load_redirect_table", return_value={}),
            # Пропускаем XLSX-экспорт — не нужен в тесте
            patch("services.database.update_service.XLSX_EXPORT_FILENAME", "tlib.xlsx"),
        ):
            result = perform_database_update(
                db_dir=db_dir,
                app_state=app_state,
                db_path=str(current_db),
                backup_pattern=DATABASE_BACKUP_PREFIX,
                retention_days=7,
                new_file_name="tlib-new.db",
            )

        return result, current_db, new_db, backup_dir, app_state

    def test_returns_true_on_success(self, tmp_path):
        result, *_ = self._run(tmp_path)
        assert result is True

    def test_trigger_file_replaced(self, tmp_path):
        _, current_db, new_db, *_ = self._run(tmp_path)
        assert current_db.exists()
        assert not new_db.exists()

    def test_backup_created(self, tmp_path):
        _, _, _, backup_dir, _ = self._run(tmp_path)
        backups = list(backup_dir.glob("tlib_*.db"))
        assert len(backups) >= 1

    def test_state_updated(self, tmp_path):
        _, _, _, _, app_state = self._run(tmp_path)
        assert hasattr(app_state, "reference_version")
        assert app_state.reference_version is not None

    def test_no_trigger_file_returns_false(self, tmp_path):
        from services.database.update_service import perform_database_update
        db_dir = tmp_path / "db_dir"
        db_dir.mkdir()
        current_db = db_dir / "tlib.db"
        _make_sqlite(current_db)
        result = perform_database_update(
            db_dir=db_dir,
            app_state=_dummy_state(),
            db_path=str(current_db),
            backup_pattern="tlib",
            retention_days=7,
            new_file_name="tlib-new.db",
        )
        assert result is False

    def test_invalid_trigger_file_returns_false(self, tmp_path):
        from services.database.update_service import perform_database_update
        db_dir = tmp_path / "db_dir"
        db_dir.mkdir()
        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        current_db = db_dir / "tlib.db"
        _make_sqlite(current_db)
        bad_new = db_dir / "tlib-new.db"
        bad_new.write_text("not a database", encoding="utf-8")
        with patch("services.database.update_service.BACKUP_DIRECTORY", str(backup_dir)):
            result = perform_database_update(
                db_dir=db_dir,
                app_state=_dummy_state(),
                db_path=str(current_db),
                backup_pattern="tlib",
                retention_days=7,
                new_file_name="tlib-new.db",
            )
        assert result is False
        assert not bad_new.exists()
