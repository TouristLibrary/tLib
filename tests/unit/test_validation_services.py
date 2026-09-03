# Version 1.1 - 12.06.2026 19:00:00 GMT
# Unit tests for services/validation/
# Описание: Проверяет чистые функции валидации данных:
#           - json_schema_validation_service: validate_json_data, read_json_file, extra-fields warning, BOM
#           - encoding_validation_service: validate_file_encoding, validate_string_encoding,
#             validate_json_encoding_detailed
#           - json_converter_service: get_file_id_from_json_name, convert_json_to_database

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# json_schema_validation_service
# ---------------------------------------------------------------------------

# Минимальная inline-схема для тестов (не загружает assets/schema.json)
_MINI_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["Шифр"],
    "additionalProperties": True,
    "properties": {
        "Шифр": {"type": "integer", "minimum": 1},
        "Маршрут": {"type": "string"},
        "Год": {"type": ["integer", "null"], "minimum": 1900, "maximum": 2100},
    },
}


class TestValidateJsonData:
    def _v(self, data, schema=None):
        from services.validation.json_schema_validation_service import validate_json_data
        return validate_json_data(data, schema or _MINI_SCHEMA)

    def test_valid_minimal_passes(self):
        ok, msg = self._v({"Шифр": 42})
        assert ok, msg

    def test_valid_full_passes(self):
        ok, msg = self._v({"Шифр": 100, "Маршрут": "Тестовый маршрут", "Год": 2024})
        assert ok, msg

    def test_missing_required_fails(self):
        ok, msg = self._v({})
        assert not ok
        assert msg

    def test_wrong_type_fails(self):
        ok, msg = self._v({"Шифр": "не число"})
        assert not ok

    def test_below_minimum_fails(self):
        ok, msg = self._v({"Шифр": 0})
        assert not ok

    def test_extra_fields_do_not_fail_with_additional_properties_true(self):
        ok, msg = self._v({"Шифр": 1, "НеизвестноеПоле": "значение"})
        assert ok, msg


class TestReadJsonFile:
    def test_reads_utf8(self, tmp_path):
        from services.validation.json_schema_validation_service import read_json_file
        p = tmp_path / "test.json"
        p.write_text('{"Шифр": 1}', encoding="utf-8")
        data = read_json_file(p)
        assert data["Шифр"] == 1

    def test_reads_utf8_bom(self, tmp_path):
        from services.validation.json_schema_validation_service import read_json_file
        p = tmp_path / "bom.json"
        # BOM + UTF-8 content
        content = '\ufeff{"Shifr": 2, "key": "val"}'
        p.write_text(content, encoding="utf-8")
        data = read_json_file(p)
        assert data["Shifr"] == 2

    def test_raises_on_invalid_json(self, tmp_path):
        from services.validation.json_schema_validation_service import read_json_file
        p = tmp_path / "bad.json"
        p.write_text("not json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            read_json_file(p)

    def test_raises_on_array_root(self, tmp_path):
        from services.validation.json_schema_validation_service import read_json_file
        p = tmp_path / "arr.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ValueError):
            read_json_file(p)


class TestValidateJsonFile:
    def test_valid_file_passes(self, tmp_path):
        from services.validation.json_schema_validation_service import validate_json_file
        p = tmp_path / "ok.json"
        p.write_text('{"Шифр": 1, "Маршрут": "тест"}', encoding="utf-8")
        schema_p = tmp_path / "schema.json"
        schema_p.write_text(json.dumps(_MINI_SCHEMA), encoding="utf-8")
        ok, msg = validate_json_file(p, schema_p)
        assert ok, msg

    def test_invalid_file_fails(self, tmp_path):
        from services.validation.json_schema_validation_service import validate_json_file
        p = tmp_path / "bad.json"
        p.write_text('{"Маршрут": "без шифра"}', encoding="utf-8")
        schema_p = tmp_path / "schema.json"
        schema_p.write_text(json.dumps(_MINI_SCHEMA), encoding="utf-8")
        ok, msg = validate_json_file(p, schema_p)
        assert not ok


# ---------------------------------------------------------------------------
# encoding_validation_service
# ---------------------------------------------------------------------------


class TestValidateFileEncoding:
    def _v(self, content: bytes, tmp_path: Path):
        from services.validation.encoding_validation_service import validate_file_encoding
        p = tmp_path / "test.json"
        p.write_bytes(content)
        return validate_file_encoding(p)

    def test_valid_utf8_passes(self, tmp_path):
        ok, msg = self._v(b'{"test": "\xd0\xa2\xd0\xb5\xd1\x81\xd1\x82"}', tmp_path)
        assert ok, msg

    def test_valid_utf8_bom_passes(self, tmp_path):
        ok, msg = self._v(b'\xef\xbb\xbf{"a": 1}', tmp_path)
        assert ok, msg

    def test_null_byte_fails(self, tmp_path):
        ok, msg = self._v(b'{"a": "\x00"}', tmp_path)
        assert not ok
        assert "null" in msg.lower() or "\\x00" in msg

    def test_surrogate_fails(self, tmp_path):
        # U+D800 — surrogate не валиден в UTF-8
        ok, msg = self._v(b'{"a": 1}\xed\xa0\x80', tmp_path)
        assert not ok

    def test_control_char_fails(self, tmp_path):
        # \x01 — управляющий символ
        ok, msg = self._v(b'{"a": "\x01"}', tmp_path)
        assert not ok

    def test_tab_newline_cr_allowed(self, tmp_path):
        ok, msg = self._v(b'{"a": "line1\nline2\ttab\rend"}', tmp_path)
        assert ok, msg


class TestValidateStringEncoding:
    def _v(self, value):
        from services.validation.encoding_validation_service import validate_string_encoding
        return validate_string_encoding(value, "TestField")

    def test_normal_string_passes(self):
        ok, _ = self._v("Привет, мир!")
        assert ok

    def test_null_byte_fails(self):
        ok, msg = self._v("hello\x00world")
        assert not ok
        assert "null" in msg.lower() or "\\x00" in msg

    def test_control_char_fails(self):
        ok, msg = self._v("text\x01char")
        assert not ok

    def test_empty_string_passes(self):
        ok, _ = self._v("")
        assert ok

    def test_non_string_passes(self):
        ok, _ = self._v(42)
        assert ok


class TestValidateJsonEncodingDetailed:
    def test_clean_json_passes(self):
        from services.validation.encoding_validation_service import validate_json_encoding_detailed
        data = {"Маршрут": "Тестовый маршрут", "Автор": "Иванов И.И."}
        ok, msg = validate_json_encoding_detailed(data)
        assert ok, msg

    def test_null_byte_in_field_fails(self):
        from services.validation.encoding_validation_service import validate_json_encoding_detailed
        data = {"Маршрут": "тест\x00плохой"}
        ok, msg = validate_json_encoding_detailed(data)
        assert not ok


# ---------------------------------------------------------------------------
# json_converter_service
# ---------------------------------------------------------------------------

# Минимальный валидный JSON-отчёт, соответствующий assets/schema.json
def _valid_report(shifr: int = 500) -> dict:
    return {
        "Шифр": shifr,
        "Маршрут": f"Тестовый маршрут {shifr}",
        "РазмерАрхива": 0,
    }


def _write_json(path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class TestGetFileIdFromJsonName:
    def _get(self, filename: str) -> str:
        from services.validation.json_converter_service import get_file_id_from_json_name
        return get_file_id_from_json_name(filename)

    def test_with_dopshifr(self):
        assert self._get("12345-а.json") == "12345-а"

    def test_without_dopshifr(self):
        assert self._get("12345.json") == "12345"

    def test_normalized_form(self):
        assert self._get("00001-TST.json") == "00001-TST"

    def test_no_extension_graceful(self):
        # Path("12345-TST").stem == "12345-TST"
        assert self._get("12345-TST") == "12345-TST"


class TestConvertJsonToDatabase:
    """Использует реальную assets/schema.json — тесты запускаются из корня проекта."""

    _SCHEMA = Path("assets/schema.json")

    def test_happy_path_two_files(self, tmp_path):
        from services.validation.json_converter_service import convert_json_to_database
        from config import DATABASE_TABLE_NAME
        import sqlite3

        src = tmp_path / "src"
        src.mkdir()
        _write_json(src / "00500-TST.json", _valid_report(500))
        _write_json(src / "00501-TST.json", _valid_report(501))
        out_db = tmp_path / "out.db"

        result = convert_json_to_database(src, self._SCHEMA, out_db)

        assert result["success_count"] == 2
        assert result["total_count"] == 2
        assert result["errors"] == []
        assert out_db.exists()

        conn = sqlite3.connect(str(out_db))
        rows = conn.execute(f"SELECT COUNT(*) FROM {DATABASE_TABLE_NAME}").fetchone()
        conn.close()
        assert rows[0] == 2

    def test_invalid_json_goes_to_errors(self, tmp_path):
        from services.validation.json_converter_service import convert_json_to_database

        src = tmp_path / "src"
        src.mkdir()
        _write_json(src / "00502-TST.json", _valid_report(502))
        # Шифр=0 нарушает minimum:1 из схемы
        _write_json(src / "00503-BAD.json", {"Шифр": 0, "Маршрут": "Плохой"})
        out_db = tmp_path / "out.db"

        result = convert_json_to_database(src, self._SCHEMA, out_db)

        assert result["success_count"] == 1
        error_files = [e["file"] for e in result["errors"]]
        assert any("00503-BAD" in f for f in error_files)

    def test_empty_directory_no_errors(self, tmp_path):
        from services.validation.json_converter_service import convert_json_to_database

        src = tmp_path / "empty"
        src.mkdir()
        out_db = tmp_path / "out.db"

        result = convert_json_to_database(src, self._SCHEMA, out_db)

        assert result["total_count"] == 0
        assert result["errors"] == []
        assert result["success_count"] == 0

    def test_json_source_as_file_list(self, tmp_path):
        from services.validation.json_converter_service import convert_json_to_database

        f1 = tmp_path / "00504-TST.json"
        f2 = tmp_path / "00505-TST.json"
        _write_json(f1, _valid_report(504))
        _write_json(f2, _valid_report(505))
        out_db = tmp_path / "out.db"

        result = convert_json_to_database([f1, f2], self._SCHEMA, out_db)

        assert result["success_count"] == 2
        assert result["errors"] == []

    def test_old_db_overwritten(self, tmp_path):
        from services.validation.json_converter_service import convert_json_to_database
        from config import DATABASE_TABLE_NAME
        import sqlite3

        src = tmp_path / "src"
        src.mkdir()
        _write_json(src / "00506-TST.json", _valid_report(506))
        out_db = tmp_path / "out.db"

        # Первый прогон — 1 запись
        convert_json_to_database(src, self._SCHEMA, out_db)

        # Добавляем второй файл и перезапускаем
        _write_json(src / "00507-TST.json", _valid_report(507))
        result = convert_json_to_database(src, self._SCHEMA, out_db)

        assert result["success_count"] == 2
        conn = sqlite3.connect(str(out_db))
        rows = conn.execute(f"SELECT COUNT(*) FROM {DATABASE_TABLE_NAME}").fetchone()
        conn.close()
        # Старая БД удалена, в новой только 2 записи
        assert rows[0] == 2
