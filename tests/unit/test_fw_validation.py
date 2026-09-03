# Version 1.1 - 12.06.2026 19:00:00 GMT
# Unit tests for services/file_watcher/validation.py
# Описание: Проверяет чистые функции валидации File Watcher pipeline:
#           validate_filename_matches_content, validate_zip_file,
#           validate_archive_consistency — без сервера, на tmp-файлах.

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# validate_filename_matches_content
# ---------------------------------------------------------------------------


class TestValidateFilenameMatchesContent:
    def _validate(self, group_id: str, json_content: dict, tmp_path: Path):
        from services.file_watcher.validation import validate_filename_matches_content
        json_file = tmp_path / f"{group_id}.json"
        json_file.write_text(json.dumps(json_content), encoding="utf-8")
        return validate_filename_matches_content(group_id, json_file)

    def test_matching_shifr_and_dopshifr(self, tmp_path):
        ok, msg = self._validate("12-FRT", {"Шифр": 12, "ДопШифр": "FRT"}, tmp_path)
        assert ok, msg

    def test_leading_zeros_in_group_id_ok(self, tmp_path):
        ok, msg = self._validate("00012-FRT", {"Шифр": 12, "ДопШифр": "FRT"}, tmp_path)
        assert ok, msg

    def test_case_insensitive_dopshifr(self, tmp_path):
        ok, msg = self._validate("00001-TST", {"Шифр": 1, "ДопШифр": "tst"}, tmp_path)
        assert ok, msg

    def test_no_dopshifr_matches_empty(self, tmp_path):
        ok, msg = self._validate("12345", {"Шифр": 12345, "ДопШифр": ""}, tmp_path)
        assert ok, msg

    def test_no_dopshifr_matches_null(self, tmp_path):
        ok, msg = self._validate("12345", {"Шифр": 12345, "ДопШифр": None}, tmp_path)
        assert ok, msg

    def test_shifr_mismatch_returns_error(self, tmp_path):
        ok, msg = self._validate("00001-TST", {"Шифр": 999, "ДопШифр": "TST"}, tmp_path)
        assert not ok
        assert msg  # непустое сообщение об ошибке

    def test_dopshifr_mismatch_returns_error(self, tmp_path):
        ok, msg = self._validate("00001-TST", {"Шифр": 1, "ДопШифр": "FRT"}, tmp_path)
        assert not ok

    def test_no_shifr_in_json_returns_error(self, tmp_path):
        ok, msg = self._validate("00001-TST", {"ДопШифр": "TST"}, tmp_path)
        assert not ok


# ---------------------------------------------------------------------------
# validate_zip_file
# ---------------------------------------------------------------------------


def _make_zip(tmp_path: Path, name: str, files: dict[str, bytes]) -> Path:
    """Создаёт ZIP с переданными файлами и возвращает путь к нему."""
    zp = tmp_path / name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, content in files.items():
            zf.writestr(fname, content)
    zp.write_bytes(buf.getvalue())
    return zp


class TestValidateZipFile:
    def test_valid_zip_returns_true(self, tmp_path):
        from services.file_watcher.validation import validate_zip_file
        zp = _make_zip(tmp_path, "valid.zip", {"doc.txt": b"hello" * 100})
        ok, msg = validate_zip_file(zp)
        assert ok, msg

    def test_bad_zip_returns_false(self, tmp_path):
        from services.file_watcher.validation import validate_zip_file
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"NOT A ZIP ARCHIVE AT ALL")
        ok, msg = validate_zip_file(bad)
        assert not ok
        assert "ZIP" in msg or "zip" in msg.lower()

    def test_oversized_zip_returns_false(self, tmp_path):
        from services.file_watcher.validation import validate_zip_file
        zp = _make_zip(tmp_path, "big.zip", {"f.txt": b"x"})
        with patch("config.MAX_ARCHIVE_SIZE", 10):
            ok, msg = validate_zip_file(zp)
        assert not ok
        assert "большой" in msg or "MB" in msg

    def test_too_many_files_returns_false(self, tmp_path):
        from services.file_watcher.validation import validate_zip_file
        files = {f"f{i}.txt": b"x" for i in range(5)}
        zp = _make_zip(tmp_path, "many.zip", files)
        with patch("config.MAX_FILES_IN_ARCHIVE", 3):
            ok, msg = validate_zip_file(zp)
        assert not ok
        assert "файлов" in msg or "файл" in msg.lower()

    def test_zip_bomb_ratio_returns_false(self, tmp_path):
        from services.file_watcher.validation import validate_zip_file
        # Файл с хорошим сжатием (много нулей) → высокий ratio
        zp = _make_zip(tmp_path, "bomb.zip", {"big.bin": b"\x00" * 50000})
        with patch("config.MAX_COMPRESSION_RATIO", 2):
            ok, msg = validate_zip_file(zp)
        assert not ok
        assert "ratio" in msg.lower() or "bomb" in msg.lower() or "ratio" in msg.lower()

    def test_empty_zip_is_valid(self, tmp_path):
        from services.file_watcher.validation import validate_zip_file
        zp = _make_zip(tmp_path, "empty.zip", {})
        ok, msg = validate_zip_file(zp)
        assert ok, msg


# ---------------------------------------------------------------------------
# validate_archive_consistency
# ---------------------------------------------------------------------------


class TestValidateArchiveConsistency:
    """Тест через реальные директории в tmp_path."""

    def _setup_dirs(self, tmp_path: Path):
        processing = tmp_path / "30_processing"
        data = tmp_path / "data"
        processing.mkdir()
        data.mkdir()
        return processing, data

    def test_json_only_no_archive_declared_is_ok(self, tmp_path):
        from services.file_watcher.validation import validate_archive_consistency
        processing, data = self._setup_dirs(tmp_path)
        (processing / "00001-TST.json").write_text(
            json.dumps({"Шифр": 1, "ДопШифр": "TST", "ТипФайла": None}), encoding="utf-8"
        )
        with (
            patch("config.UPLOAD_PROCESSING_DIRECTORY", str(processing)),
            patch("config.DATA_DIRECTORY", str(data)),
        ):
            ok, msg = validate_archive_consistency("00001-TST")
        assert ok, msg

    def test_json_and_zip_match_is_ok(self, tmp_path):
        from services.file_watcher.validation import validate_archive_consistency
        processing, data = self._setup_dirs(tmp_path)
        (processing / "00001-TST.json").write_text(
            json.dumps({"Шифр": 1, "ДопШифр": "TST", "ТипФайла": "zip"}), encoding="utf-8"
        )
        (processing / "00001-TST.zip").write_bytes(b"PK")
        with (
            patch("config.UPLOAD_PROCESSING_DIRECTORY", str(processing)),
            patch("config.DATA_DIRECTORY", str(data)),
        ):
            ok, msg = validate_archive_consistency("00001-TST")
        assert ok, msg

    def test_tipfaila_pdf_but_zip_present_is_error(self, tmp_path):
        from services.file_watcher.validation import validate_archive_consistency
        processing, data = self._setup_dirs(tmp_path)
        (processing / "00001-TST.json").write_text(
            json.dumps({"Шифр": 1, "ДопШифр": "TST", "ТипФайла": "pdf"}), encoding="utf-8"
        )
        (processing / "00001-TST.zip").write_bytes(b"PK")
        with (
            patch("config.UPLOAD_PROCESSING_DIRECTORY", str(processing)),
            patch("config.DATA_DIRECTORY", str(data)),
        ):
            ok, msg = validate_archive_consistency("00001-TST")
        assert not ok
        assert "pdf" in msg.lower() or "zip" in msg.lower()

    def test_two_archives_in_processing_is_error(self, tmp_path):
        from services.file_watcher.validation import validate_archive_consistency
        processing, data = self._setup_dirs(tmp_path)
        (processing / "00001-TST.json").write_text(
            json.dumps({"Шифр": 1, "ДопШифр": "TST", "ТипФайла": "zip"}), encoding="utf-8"
        )
        (processing / "00001-TST.zip").write_bytes(b"PK")
        (processing / "00001-TST.pdf").write_bytes(b"%PDF")
        with (
            patch("config.UPLOAD_PROCESSING_DIRECTORY", str(processing)),
            patch("config.DATA_DIRECTORY", str(data)),
        ):
            ok, msg = validate_archive_consistency("00001-TST")
        assert not ok
        assert "несколько" in msg.lower() or "архив" in msg.lower()


