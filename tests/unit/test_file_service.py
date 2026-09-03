# Version 1.0 - 12.06.2026 18:00:00 GMT
# Unit tests for services/file_service.py
# Описание: Проверяет функции работы с именами файлов из ZIP-архивов:
#           decode_zip_filename (порядок ZIP_ENCODINGS, кракозябры CP437 → кириллица),
#           _has_mojibake, is_macos_metadata_file.

from __future__ import annotations

import unittest


# ---------------------------------------------------------------------------
# _has_mojibake
# ---------------------------------------------------------------------------


class TestHasMojibake(unittest.TestCase):
    def _check(self, text: str) -> bool:
        from services.file_service import _has_mojibake
        return _has_mojibake(text)

    def test_clean_cyrillic_no_mojibake(self):
        self.assertFalse(self._check("Отчёт горный 2024.zip"))

    def test_latin_no_mojibake(self):
        self.assertFalse(self._check("report.zip"))

    def test_box_drawing_is_mojibake(self):
        # U+2500 — box drawing character (появляется при CP437 decode)
        self.assertTrue(self._check("text\u2500more"))

    def test_greek_letter_is_mojibake(self):
        # U+0370 — начало греческого диапазона
        self.assertTrue(self._check("text\u0370more"))

    def test_empty_string_no_mojibake(self):
        self.assertFalse(self._check(""))

    def test_upper_boundary_box_drawing(self):
        # U+257F — последний box drawing
        self.assertTrue(self._check("\u257f"))

    def test_outside_range_no_mojibake(self):
        # U+2480 — вне диапазонов
        self.assertFalse(self._check("\u2480"))


# ---------------------------------------------------------------------------
# is_macos_metadata_file
# ---------------------------------------------------------------------------


class TestIsMacosMetadataFile(unittest.TestCase):
    def _check(self, filename: str) -> bool:
        from services.file_service import is_macos_metadata_file
        return is_macos_metadata_file(filename)

    def test_macosx_folder_root(self):
        self.assertTrue(self._check("__MACOSX/file.txt"))

    def test_macosx_folder_nested(self):
        self.assertTrue(self._check("folder/__MACOSX/file.txt"))

    def test_dot_underscore_root(self):
        self.assertTrue(self._check("._file.txt"))

    def test_dot_underscore_in_folder(self):
        self.assertTrue(self._check("folder/._file.txt"))

    def test_normal_file_false(self):
        self.assertFalse(self._check("normal_file.txt"))

    def test_normal_folder_file_false(self):
        self.assertFalse(self._check("docs/report.pdf"))

    def test_partial_macosx_not_matched(self):
        # Не начинается с __MACOSX/ и не содержит /__MACOSX/
        self.assertFalse(self._check("notmacosx/file.txt"))


# ---------------------------------------------------------------------------
# decode_zip_filename
# ---------------------------------------------------------------------------


class TestDecodeZipFilename(unittest.TestCase):
    def _decode(self, filename):
        from services.file_service import decode_zip_filename
        return decode_zip_filename(filename)

    def test_clean_utf8_string_returned_as_is(self):
        result = self._decode("Горный маршрут.zip")
        self.assertEqual(result, "Горный маршрут.zip")

    def test_clean_latin_string_returned_as_is(self):
        result = self._decode("report.zip")
        self.assertEqual(result, "report.zip")

    def test_bytes_utf8_decoded_correctly(self):
        # UTF-8 bytes for "Отчёт.zip"
        raw = "Отчёт.zip".encode("utf-8")
        result = self._decode(raw)
        self.assertEqual(result, "Отчёт.zip")

    def test_bytes_cp866_decoded_correctly(self):
        # CP866 bytes for "Маршрут.zip"
        raw = "Маршрут.zip".encode("cp866")
        result = self._decode(raw)
        self.assertEqual(result, "Маршрут.zip")

    def test_bytes_cp1251_decoded_correctly(self):
        # CP1251 bytes for "Отчёт 2024.zip"
        raw = "Отчёт 2024.zip".encode("cp1251")
        result = self._decode(raw)
        self.assertEqual(result, "Отчёт 2024.zip")

    def test_mojibake_string_fixed(self):
        # Имитируем кракозябры: кодируем в CP866, декодируем как CP437 (Python zipfile default)
        original = "Маршрут.zip"
        mojibake = original.encode("cp866").decode("cp437")
        result = self._decode(mojibake)
        self.assertEqual(result, original)

    def test_cp866_before_cp1251_order(self):
        """CP866 проверяется раньше CP1251 — убеждаемся, что DOS-кодировки не перепутаны."""
        from config import ZIP_ENCODINGS
        if "cp866" in ZIP_ENCODINGS and "cp1251" in ZIP_ENCODINGS:
            idx866 = ZIP_ENCODINGS.index("cp866")
            idx1251 = ZIP_ENCODINGS.index("cp1251")
            self.assertLess(idx866, idx1251, "cp866 должен идти раньше cp1251 в ZIP_ENCODINGS")


if __name__ == "__main__":
    unittest.main()
