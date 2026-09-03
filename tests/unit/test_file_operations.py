# Version 1.0 - 26.07.2026 09:00:00 GMT
# Unit tests for services/file_watcher/file_operations.py
# Описание: Проверяет canonicalize_json_dopshifr — канонизацию поля ДопШифр в JSON к UPPERCASE.

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


class TestCanonicalizeJsonDopshifr:
    """Тесты canonicalize_json_dopshifr: патч UPLOAD_PROCESSING_DIRECTORY на tmp_path."""

    def _run(self, tmp_path: Path, group_id: str, data: dict) -> bool:
        from services.file_watcher.file_operations import canonicalize_json_dopshifr
        json_file = tmp_path / f"{group_id}.json"
        json_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        with patch("config.UPLOAD_PROCESSING_DIRECTORY", str(tmp_path)):
            return canonicalize_json_dopshifr(group_id)

    def test_lowercase_normalized_to_upper(self, tmp_path):
        """lowercase ДопШифр → перезаписан как UPPERCASE, вернул True."""
        from services.file_watcher.file_operations import canonicalize_json_dopshifr

        group_id = "00001-TST"
        json_file = tmp_path / f"{group_id}.json"
        json_file.write_text(
            json.dumps({"Шифр": 1, "ДопШифр": "tst"}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        with patch("config.UPLOAD_PROCESSING_DIRECTORY", str(tmp_path)):
            result = canonicalize_json_dopshifr(group_id)

        assert result is True
        saved = json.loads(json_file.read_text(encoding="utf-8"))
        assert saved["ДопШифр"] == "TST"

    def test_already_uppercase_no_rewrite(self, tmp_path):
        """Уже UPPERCASE → файл не перезаписывается (байты идентичны)."""
        from services.file_watcher.file_operations import canonicalize_json_dopshifr

        group_id = "00001-TST"
        json_file = tmp_path / f"{group_id}.json"
        json_file.write_text(
            json.dumps({"Шифр": 1, "ДопШифр": "TST"}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        original_bytes = json_file.read_bytes()

        with patch("config.UPLOAD_PROCESSING_DIRECTORY", str(tmp_path)):
            result = canonicalize_json_dopshifr(group_id)

        assert result is True
        assert json_file.read_bytes() == original_bytes

    def test_none_dopshifr_noop(self, tmp_path):
        """ДопШифр: None → no-op, True, файл не тронут."""
        from services.file_watcher.file_operations import canonicalize_json_dopshifr

        group_id = "00001"
        json_file = tmp_path / f"{group_id}.json"
        json_file.write_text(
            json.dumps({"Шифр": 1, "ДопШифр": None}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        original_bytes = json_file.read_bytes()

        with patch("config.UPLOAD_PROCESSING_DIRECTORY", str(tmp_path)):
            result = canonicalize_json_dopshifr(group_id)

        assert result is True
        assert json_file.read_bytes() == original_bytes

    def test_missing_dopshifr_field_noop(self, tmp_path):
        """Поле ДопШифр отсутствует → no-op, True, файл не тронут."""
        from services.file_watcher.file_operations import canonicalize_json_dopshifr

        group_id = "00001"
        json_file = tmp_path / f"{group_id}.json"
        json_file.write_text(
            json.dumps({"Шифр": 1}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        original_bytes = json_file.read_bytes()

        with patch("config.UPLOAD_PROCESSING_DIRECTORY", str(tmp_path)):
            result = canonicalize_json_dopshifr(group_id)

        assert result is True
        assert json_file.read_bytes() == original_bytes

    def test_missing_json_file_returns_false(self, tmp_path):
        """JSON файл отсутствует → False, без исключения."""
        from services.file_watcher.file_operations import canonicalize_json_dopshifr

        with patch("config.UPLOAD_PROCESSING_DIRECTORY", str(tmp_path)):
            result = canonicalize_json_dopshifr("00099-NON")

        assert result is False

    def test_corrupt_json_returns_false(self, tmp_path):
        """Битый JSON → False, без исключения."""
        from services.file_watcher.file_operations import canonicalize_json_dopshifr

        group_id = "00002-BAD"
        json_file = tmp_path / f"{group_id}.json"
        json_file.write_text("not a json {{{", encoding="utf-8")

        with patch("config.UPLOAD_PROCESSING_DIRECTORY", str(tmp_path)):
            result = canonicalize_json_dopshifr(group_id)

        assert result is False
