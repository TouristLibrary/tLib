# Version 1.0 - 12.06.2026 20:00:00 GMT
# Unit tests for services/database/reference_loader.py
# Описание: Проверяет load_reference_lists (справочники, категории, отчёт-счётчик)
#           и load_redirect_table (маппинг СтарыйID → Шифр).
#           Пути к БД передаются параметром — патчинг config не нужен.
#           tmp-БД создаётся через build_create_table_sql/build_insert_sql.

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from config import DATABASE_TABLE_NAME
from services.database.tlib_table_spec import (
    build_create_table_sql,
    build_insert_sql,
    build_values,
)


# ---------------------------------------------------------------------------
# Хелпер: создание тестовой БД
# ---------------------------------------------------------------------------


def _make_db(path: Path, rows: list[dict]) -> str:
    """Создаёт tlib.db с переданными строками, возвращает строковый путь."""
    conn = sqlite3.connect(str(path))
    conn.execute(build_create_table_sql(DATABASE_TABLE_NAME))
    for row in rows:
        conn.execute(build_insert_sql(DATABASE_TABLE_NAME), build_values(row))
    conn.commit()
    conn.close()
    return str(path)


# ---------------------------------------------------------------------------
# load_reference_lists
# ---------------------------------------------------------------------------


class TestLoadReferenceLists:
    def _load(self, db_path: str) -> dict:
        from services.database.reference_loader import load_reference_lists
        return load_reference_lists(db_path)

    def test_empty_db_returns_default_prefixes(self, tmp_path):
        db = _make_db(tmp_path / "tlib.db", [])
        result = self._load(db)
        # ДопШифр начинается с ["", "нет"]
        assert result["dopshifr_list"][:2] == ["", "нет"]
        # Остальные справочники начинаются с [""]
        assert result["raion_obshiy_list"][0] == ""
        assert result["tip_list"][0] == ""

    def test_db_with_records_fills_lists(self, tmp_path):
        db = _make_db(tmp_path / "tlib.db", [
            {"Шифр": 1, "ДопШифр": "TST", "РайонОбщий": "Кавказ", "Тип": "горный",
             "КатегорияС": "3", "КатегорияПо": "4"},
            {"Шифр": 2, "ДопШифр": "NEW", "РайонОбщий": "Урал", "Тип": "водный",
             "КатегорияС": "2", "КатегорияПо": "3"},
        ])
        result = self._load(db)
        assert "TST" in result["dopshifr_list"]
        assert "NEW" in result["dopshifr_list"]
        assert "Кавказ" in result["raion_obshiy_list"]
        assert "горный" in result["tip_list"]

    def test_reports_count_matches_rows(self, tmp_path):
        db = _make_db(tmp_path / "tlib.db", [
            {"Шифр": 1}, {"Шифр": 2}, {"Шифр": 3},
        ])
        from config import STATE_REPORTS_COUNT
        result = self._load(db)
        assert result[STATE_REPORTS_COUNT] == 3

    def test_kategoria_unified_sorted_and_starts_with_empty(self, tmp_path):
        db = _make_db(tmp_path / "tlib.db", [
            {"Шифр": 1, "КатегорияС": "3", "КатегорияПо": "4"},
            {"Шифр": 2, "КатегорияС": "1", "КатегорияПо": "2"},
        ])
        from config import STATE_KATEGORIA_UNIFIED
        result = self._load(db)
        unified = result[STATE_KATEGORIA_UNIFIED]
        assert unified[0] == ""
        # Числовые категории должны быть отсортированы по возрастанию сложности
        actual_cats = [c for c in unified if c]
        assert actual_cats == sorted(actual_cats, key=lambda c: c)  # хотя бы не перепутаны

    def test_nonexistent_db_returns_defaults(self):
        from services.database.reference_loader import load_reference_lists
        from config import get_default_reference_values
        result = load_reference_lists("/nonexistent/path/tlib.db")
        defaults = get_default_reference_values()
        assert result["dopshifr_list"] == defaults["dopshifr_list"]

    def test_corrupt_file_returns_defaults(self, tmp_path):
        bad = tmp_path / "bad.db"
        bad.write_text("not a database", encoding="utf-8")
        from services.database.reference_loader import load_reference_lists
        from config import get_default_reference_values
        result = load_reference_lists(str(bad))
        defaults = get_default_reference_values()
        assert result["dopshifr_list"] == defaults["dopshifr_list"]


# ---------------------------------------------------------------------------
# load_redirect_table
# ---------------------------------------------------------------------------


class TestLoadRedirectTable:
    def _load(self, db_path: str) -> dict:
        from services.database.reference_loader import load_redirect_table
        return load_redirect_table(db_path)

    def test_record_with_old_id_and_dopshifr(self, tmp_path):
        db = _make_db(tmp_path / "tlib.db", [
            {"Шифр": 100, "ДопШифр": "TLIB", "СтарыйID": 28462},
        ])
        result = self._load(db)
        assert result.get("id=28462") == "100-TLIB"

    def test_record_with_old_id_no_dopshifr(self, tmp_path):
        db = _make_db(tmp_path / "tlib.db", [
            {"Шифр": 200, "ДопШифр": None, "СтарыйID": 9999},
        ])
        result = self._load(db)
        assert result.get("id=9999") == "200"

    def test_records_without_old_id_excluded(self, tmp_path):
        db = _make_db(tmp_path / "tlib.db", [
            {"Шифр": 1, "ДопШифр": "TST"},           # нет СтарыйID
            {"Шифр": 2, "ДопШифр": "NEW", "СтарыйID": 12345},
        ])
        result = self._load(db)
        assert len(result) == 1
        assert "id=12345" in result

    def test_nonexistent_db_returns_empty(self):
        from services.database.reference_loader import load_redirect_table
        result = load_redirect_table("/nonexistent/path/tlib.db")
        assert result == {}
