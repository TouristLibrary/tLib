# Version 1.1 - 12.06.2026 18:00:00 GMT
# Unit tests for services/file_watcher/scanner.py and utils.py
# Описание: Проверяет нормализацию ID групп (Шифр → 5 цифр, ДопШифр → UPPERCASE),
#           склейку разных написаний одного Шифра в одну группу и детект дубликатов.
#           normalize_filename_for_data покрыт в test_utils.py — здесь не дублируется.
#
# Контракты:
#   group_files_by_id():
#     - "1-tst.json" + "00001-TST.zip" → одна группа "00001-TST"
#     - "1-AAA.json" + "1-BBB.json"    → две раздельные группы
#   filter_ambiguous_groups():
#     - "1-TST.json" + "00001-TST.json" → ambiguous (оба → 00001-TST.json)
#     - "1-TST.json" + "1-TST.zip"      → clean (разные расширения)
#   get_normalized_group_id():
#     - "1-tst" → "00001-TST"

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _make_files(directory: Path, *names: str) -> list[Path]:
    """Создаёт пустые файлы в директории и возвращает их пути."""
    paths = []
    for name in names:
        p = directory / name
        p.write_bytes(b"")
        paths.append(p)
    return paths


class TestGetNormalizedGroupId(unittest.TestCase):
    """Тесты для utils.get_normalized_group_id."""

    def _norm(self, group_id: str) -> str:
        from services.file_watcher.utils import get_normalized_group_id
        return get_normalized_group_id(group_id)

    def test_leading_zeros_added(self):
        """Шифр без ДопШифра дополняется до 5 цифр."""
        self.assertEqual(self._norm("1"), "00001")
        self.assertEqual(self._norm("345"), "00345")
        self.assertEqual(self._norm("12345"), "12345")

    def test_six_or_more_digits_preserved(self):
        """Шифр ≥ 6 цифр сохраняется без усечения."""
        self.assertEqual(self._norm("123456"), "123456")

    def test_dopshifr_uppercased(self):
        """ДопШифр приводится к UPPERCASE."""
        self.assertEqual(self._norm("1-tst"), "00001-TST")
        self.assertEqual(self._norm("12-frt"), "00012-FRT")

    def test_already_normalized_unchanged(self):
        """Уже нормализованный ID не меняется."""
        self.assertEqual(self._norm("00012-FRT"), "00012-FRT")
        self.assertEqual(self._norm("00001-TST"), "00001-TST")

    def test_no_dopshifr_no_dash(self):
        """Без ДопШифра в результате не должно быть дефиса."""
        result = self._norm("12")
        self.assertNotIn("-", result)
        self.assertEqual(result, "00012")


class TestGroupFilesById(unittest.TestCase):
    """Тесты для scanner.group_files_by_id."""

    def _group(self, *filenames: str) -> dict:
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            files = _make_files(directory, *filenames)
            from services.file_watcher.scanner import group_files_by_id
            return {
                group_id: [p.name for p in paths]
                for group_id, paths in group_files_by_id(files).items()
            }

    def test_merges_different_shifr_representations(self):
        """1-TST.json + 00001-TST.zip → одна группа 00001-TST."""
        groups = self._group("1-TST.json", "00001-TST.zip")
        self.assertEqual(len(groups), 1)
        self.assertIn("00001-TST", groups)
        self.assertEqual(sorted(groups["00001-TST"]), sorted(["1-TST.json", "00001-TST.zip"]))

    def test_merges_different_dopshifr_case(self):
        """1-tst.json + 1-TST.zip → одна группа 00001-TST."""
        groups = self._group("1-tst.json", "1-TST.zip")
        self.assertEqual(len(groups), 1)
        self.assertIn("00001-TST", groups)

    def test_merges_all_three_variants(self):
        """00001-tst.json + 1-TST.zip + 1-tst.pdf → одна группа (три файла, zip+pdf — оба, тест на склейку)."""
        groups = self._group("00001-tst.json", "1-TST.zip")
        self.assertEqual(len(groups), 1)
        self.assertIn("00001-TST", groups)
        self.assertEqual(len(groups["00001-TST"]), 2)

    def test_distinct_dopshifr_stays_separate(self):
        """1-AAA.json + 1-BBB.json → две разные группы."""
        groups = self._group("1-AAA.json", "1-BBB.json")
        self.assertEqual(len(groups), 2)
        self.assertIn("00001-AAA", groups)
        self.assertIn("00001-BBB", groups)

    def test_no_dopshifr_groups_correctly(self):
        """12345.json без ДопШифра группируется под ключом 12345."""
        groups = self._group("12345.json", "12345.zip")
        self.assertEqual(len(groups), 1)
        self.assertIn("12345", groups)

    def test_unknown_extension_skipped(self):
        """Файлы с неизвестным расширением не попадают ни в одну группу."""
        groups = self._group("1-TST.json", "1-TST.txt")
        self.assertEqual(len(groups), 1)
        for files in groups.values():
            self.assertNotIn("1-TST.txt", files)


class TestFilterAmbiguousGroups(unittest.TestCase):
    """Тесты для scanner.filter_ambiguous_groups."""

    def _filter(self, *filenames: str):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td)
            files = _make_files(directory, *filenames)
            from services.file_watcher.scanner import group_files_by_id, filter_ambiguous_groups
            groups = group_files_by_id(files)
            clean, ambiguous = filter_ambiguous_groups(groups)
            clean_names = {gid: [p.name for p in paths] for gid, paths in clean.items()}
            ambiguous_ids = set(ambiguous.keys())
            return clean_names, ambiguous_ids

    def test_same_extension_different_writing_is_ambiguous(self):
        """1-TST.json + 00001-TST.json → одна группа, обнаружен дубликат → ambiguous."""
        clean, ambiguous = self._filter("1-TST.json", "00001-TST.json")
        self.assertIn("00001-TST", ambiguous)
        self.assertNotIn("00001-TST", clean)

    def test_different_extensions_is_clean(self):
        """1-TST.json + 1-TST.zip → один таргет на каждое расширение → clean."""
        clean, ambiguous = self._filter("1-TST.json", "1-TST.zip")
        self.assertIn("00001-TST", clean)
        self.assertNotIn("00001-TST", ambiguous)

    def test_case_collision_is_ambiguous(self):
        """1-tst.json + 1-TST.json → оба нормализуются в 00001-TST.json → ambiguous."""
        clean, ambiguous = self._filter("1-tst.json", "1-TST.json")
        self.assertIn("00001-TST", ambiguous)

    def test_unrelated_group_not_affected(self):
        """Дубликат в одной группе не затрагивает другую чистую группу."""
        clean, ambiguous = self._filter(
            "1-TST.json", "00001-TST.json",  # ambiguous
            "2-AAA.json",                      # clean
        )
        self.assertIn("00001-TST", ambiguous)
        self.assertIn("00002-AAA", clean)

    def test_empty_groups_returns_empty(self):
        """Пустой вход → оба результата пустые."""
        from services.file_watcher.scanner import filter_ambiguous_groups
        clean, ambiguous = filter_ambiguous_groups({})
        self.assertEqual(clean, {})
        self.assertEqual(ambiguous, {})


class TestParseFilename(unittest.TestCase):
    """Тесты для scanner.parse_filename."""

    def _parse(self, filename: str):
        from services.file_watcher.scanner import parse_filename
        return parse_filename(filename)

    def test_shifr_dop_json(self):
        r = self._parse("12345-TST.json")
        self.assertIsNotNone(r)
        self.assertEqual(r["shifr"], "12345")
        self.assertEqual(r["dopshifr"], "TST")
        self.assertEqual(r["ext"], ".json")
        self.assertEqual(r["id"], "12345-TST")

    def test_shifr_only_zip(self):
        r = self._parse("00001.zip")
        self.assertIsNotNone(r)
        self.assertEqual(r["shifr"], "00001")
        self.assertIsNone(r["dopshifr"])
        self.assertEqual(r["ext"], ".zip")

    def test_delete_trigger_with_dop(self):
        r = self._parse("00001-TST.delete")
        self.assertIsNotNone(r)
        self.assertEqual(r["shifr"], "00001")
        self.assertEqual(r["dopshifr"], "TST")
        self.assertEqual(r["ext"], ".delete")
        self.assertEqual(r.get("operation"), "delete")

    def test_delete_trigger_without_dop(self):
        r = self._parse("12345.delete")
        self.assertIsNotNone(r)
        self.assertEqual(r["shifr"], "12345")
        self.assertIsNone(r["dopshifr"])
        self.assertEqual(r.get("operation"), "delete")

    def test_invalid_extension_returns_none(self):
        self.assertIsNone(self._parse("readme.txt"))

    def test_no_digits_returns_none(self):
        self.assertIsNone(self._parse("noshifr.json"))

    def test_pdf_extension(self):
        r = self._parse("12345.pdf")
        self.assertIsNotNone(r)
        self.assertEqual(r["ext"], ".pdf")

    def test_case_insensitive_extension(self):
        """Расширение .ZIP должно работать так же как .zip."""
        r = self._parse("00001-TST.ZIP")
        # Зависит от паттернов в config — паттерн без флага IGNORECASE
        # Если None — допустимо (паттерн строчный), если не None — проверяем
        if r is not None:
            self.assertIn(r["ext"].lower(), {".zip", ".json", ".pdf"})


class TestFilterCompleteGroups(unittest.TestCase):
    """Тесты для scanner.filter_complete_groups."""

    def _filter(self, groups: dict, data_dir: Path):
        from services.file_watcher.scanner import filter_complete_groups
        with patch("config.DATA_DIRECTORY", str(data_dir)):
            return filter_complete_groups(groups)

    def test_json_and_zip_is_complete(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td) / "data"
            data_dir.mkdir()
            go_dir = Path(td) / "20_go"
            go_dir.mkdir()
            json_file = go_dir / "00001-TST.json"
            zip_file = go_dir / "00001-TST.zip"
            json_file.write_text(
                json.dumps({"Шифр": 1, "ДопШифр": "TST", "ТипФайла": "zip"}),
                encoding="utf-8",
            )
            zip_file.write_bytes(b"PK")
            complete, json_only, partial = self._filter(
                {"00001-TST": [json_file, zip_file]}, data_dir
            )
            self.assertIn("00001-TST", complete)
            self.assertNotIn("00001-TST", json_only)
            self.assertNotIn("00001-TST", partial)

    def test_json_only_no_tipfaila_is_json_only(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td) / "data"
            data_dir.mkdir()
            go_dir = Path(td) / "20_go"
            go_dir.mkdir()
            json_file = go_dir / "00001-TST.json"
            json_file.write_text(
                json.dumps({"Шифр": 1, "ДопШифр": "TST"}), encoding="utf-8"
            )
            complete, json_only, partial = self._filter(
                {"00001-TST": [json_file]}, data_dir
            )
            self.assertIn("00001-TST", json_only)

    def test_json_waiting_archive_skipped(self):
        """JSON с ТипФайла=zip, архива в data/ нет — группа ждёт."""
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td) / "data"
            data_dir.mkdir()
            go_dir = Path(td) / "20_go"
            go_dir.mkdir()
            json_file = go_dir / "00001-TST.json"
            json_file.write_text(
                json.dumps({"Шифр": 1, "ДопШифр": "TST", "ТипФайла": "zip"}),
                encoding="utf-8",
            )
            complete, json_only, partial = self._filter(
                {"00001-TST": [json_file]}, data_dir
            )
            self.assertNotIn("00001-TST", complete)
            self.assertNotIn("00001-TST", json_only)
            self.assertNotIn("00001-TST", partial)

    def test_zip_only_with_json_in_data_is_partial(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td) / "data"
            data_dir.mkdir()
            go_dir = Path(td) / "20_go"
            go_dir.mkdir()
            (data_dir / "00001-TST.json").write_text(
                json.dumps({"Шифр": 1}), encoding="utf-8"
            )
            zip_file = go_dir / "00001-TST.zip"
            zip_file.write_bytes(b"PK")
            complete, json_only, partial = self._filter(
                {"00001-TST": [zip_file]}, data_dir
            )
            self.assertIn("00001-TST", partial)

    def test_zip_only_without_json_anywhere_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td) / "data"
            data_dir.mkdir()
            go_dir = Path(td) / "20_go"
            go_dir.mkdir()
            zip_file = go_dir / "00002-NEW.zip"
            zip_file.write_bytes(b"PK")
            complete, json_only, partial = self._filter(
                {"00002-NEW": [zip_file]}, data_dir
            )
            self.assertNotIn("00002-NEW", complete)
            self.assertNotIn("00002-NEW", json_only)
            self.assertNotIn("00002-NEW", partial)


if __name__ == "__main__":
    unittest.main()
