# Version 1.0 - 14.06.2026 18:20:00 GMT
# Unit tests for services/database/connection.py
# Описание: Проверяет open_tlib_db():
#           - read-only режим запрещает запись (OperationalError на INSERT)
#           - row_factory=True возвращает sqlite3.Row, False — tuple
#           - register_lower корректно понижает кириллицу через SQL-функцию LOWER
#           Всё на tmp-файлах, без живой БД приложения.

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from services.database.connection import open_tlib_db


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _make_db(path: Path) -> Path:
    """Создаёт минимальную SQLite БД с тестовой таблицей и одной строкой."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE items (id INTEGER, name TEXT)")
    conn.execute("INSERT INTO items VALUES (1, 'Привет')")
    conn.commit()
    conn.close()
    return path


# ---------------------------------------------------------------------------
# read_only — запись запрещена
# ---------------------------------------------------------------------------

class TestReadOnly:
    def test_insert_raises_on_readonly(self, tmp_path):
        """INSERT на read-only соединении выбрасывает OperationalError."""
        db = _make_db(tmp_path / "test.db")
        conn = open_tlib_db(str(db), read_only=True)
        try:
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("INSERT INTO items VALUES (2, 'test')")
        finally:
            conn.close()

    def test_select_works_on_readonly(self, tmp_path):
        """SELECT корректно работает в read-only режиме."""
        db = _make_db(tmp_path / "test.db")
        conn = open_tlib_db(str(db), read_only=True)
        try:
            row = conn.execute("SELECT id FROM items WHERE id = 1").fetchone()
            assert row is not None
        finally:
            conn.close()

    def test_readwrite_allows_insert(self, tmp_path):
        """При read_only=False запись разрешена (проверка что флаг работает)."""
        db = _make_db(tmp_path / "test.db")
        conn = open_tlib_db(str(db), read_only=False)
        try:
            conn.execute("INSERT INTO items VALUES (2, 'world')")
            conn.commit()
            count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            assert count == 2
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# row_factory
# ---------------------------------------------------------------------------

class TestRowFactory:
    def test_row_factory_on_returns_sqlite_row(self, tmp_path):
        """row_factory=True (по умолчанию) возвращает sqlite3.Row."""
        db = _make_db(tmp_path / "test.db")
        conn = open_tlib_db(str(db), row_factory=True)
        try:
            row = conn.execute("SELECT id, name FROM items").fetchone()
            assert isinstance(row, sqlite3.Row)
            assert row["id"] == 1
        finally:
            conn.close()

    def test_row_factory_off_returns_tuple(self, tmp_path):
        """row_factory=False возвращает обычный tuple."""
        db = _make_db(tmp_path / "test.db")
        conn = open_tlib_db(str(db), row_factory=False)
        try:
            row = conn.execute("SELECT id, name FROM items").fetchone()
            assert isinstance(row, tuple)
            assert row[0] == 1
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# register_lower — поддержка кириллицы
# ---------------------------------------------------------------------------

class TestRegisterLower:
    def test_lower_reduces_cyrillic(self, tmp_path):
        """UDF LOWER корректно понижает кириллические строки."""
        db = _make_db(tmp_path / "test.db")
        conn = open_tlib_db(str(db), register_lower=True)
        try:
            row = conn.execute("SELECT LOWER('ПРИВЕТ')").fetchone()
            assert row[0] == "привет"
        finally:
            conn.close()

    def test_lower_handles_none(self, tmp_path):
        """UDF LOWER возвращает None для NULL (не падает)."""
        db = _make_db(tmp_path / "test.db")
        conn = open_tlib_db(str(db), register_lower=True)
        try:
            row = conn.execute("SELECT LOWER(NULL)").fetchone()
            assert row[0] is None
        finally:
            conn.close()

    def test_no_lower_without_flag(self, tmp_path):
        """Без register_lower встроенный LOWER не понижает кириллицу."""
        db = _make_db(tmp_path / "test.db")
        conn = open_tlib_db(str(db), register_lower=False)
        try:
            row = conn.execute("SELECT LOWER('ПРИВЕТ')").fetchone()
            # Встроенный SQLite LOWER не трогает кириллицу — строка не меняется
            assert row[0] == "ПРИВЕТ"
        finally:
            conn.close()
