import unittest
from services.file_watcher.utils import normalize_filename_for_data


class TestNormalizeFilenameForData(unittest.TestCase):
    """Канонизация имени: Шифр → 5 цифр, ДопШифр → UPPERCASE. Работает для всех расширений, включая .delete."""

    def test_delete_short_shifr_uppercase_dop(self):
        self.assertEqual(normalize_filename_for_data("1-TST.delete"), "00001-TST.delete")

    def test_delete_short_shifr_lowercase_dop(self):
        self.assertEqual(normalize_filename_for_data("1-tst.delete"), "00001-TST.delete")

    def test_delete_full_shifr_lowercase_dop(self):
        self.assertEqual(normalize_filename_for_data("00001-tst.delete"), "00001-TST.delete")

    def test_delete_already_canonical_is_idempotent(self):
        self.assertEqual(normalize_filename_for_data("00001-TST.delete"), "00001-TST.delete")

    def test_delete_without_dopshifr(self):
        self.assertEqual(normalize_filename_for_data("1.delete"), "00001.delete")

    def test_json_short_shifr_still_normalized(self):
        # Regression-проверка: обычные расширения не сломаны
        self.assertEqual(normalize_filename_for_data("1-TST.json"), "00001-TST.json")

    def test_unparseable_filename_returned_as_is(self):
        self.assertEqual(normalize_filename_for_data("random.txt"), "random.txt")


if __name__ == "__main__":
    unittest.main()
