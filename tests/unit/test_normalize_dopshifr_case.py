# Version 1.0 - 26.07.2026 08:40:00 GMT
# Тесты для tools/normalize_dopshifr_case.py
# Описание: Проверяет две чистые функции утилиты:
#           _target_name — вычисление целевого имени файла после нормализации ДопШифра,
#           _patch_json_text — минимальная текстовая правка поля "ДопШифр" в JSON.

import json
import sys
from pathlib import Path

import pytest

# Добавляем корень проекта в sys.path (утилита лежит в tools/, тесты в tests/unit/)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from tools.normalize_dopshifr_case import _patch_json_text, _target_name  # noqa: E402

UTF8_BOM = b"\xef\xbb\xbf"


# ---------------------------------------------------------------------------
# _target_name
# ---------------------------------------------------------------------------

class TestTargetName:
    def test_lowercase_dop_json(self):
        assert _target_name("00012-frt.json") == "00012-FRT.json"

    def test_lowercase_dop_zip(self):
        assert _target_name("00345-tst.zip") == "00345-TST.zip"

    def test_lowercase_dop_pdf(self):
        assert _target_name("00001-abc.pdf") == "00001-ABC.pdf"

    def test_already_upper_returns_none(self):
        assert _target_name("00012-FRT.json") is None

    def test_mixed_case_dop(self):
        assert _target_name("00001-Tst.zip") == "00001-TST.zip"

    def test_no_dopshifr_returns_none(self):
        assert _target_name("00012.json") is None

    def test_no_dopshifr_pdf(self):
        assert _target_name("12345.pdf") is None

    def test_unrecognized_filename_returns_none(self):
        # Не подпадает под паттерн
        assert _target_name("README.md") is None

    def test_shifr_preserved_as_is(self):
        # Шифр в имени файла не нормализуется нулями — берётся как есть
        result = _target_name("12-frt.json")
        assert result == "12-FRT.json"

    def test_cyrillic_dop(self):
        # Кириллица в ДопШифре
        assert _target_name("00001-тлб.json") == "00001-ТЛБ.json"


# ---------------------------------------------------------------------------
# _patch_json_text
# ---------------------------------------------------------------------------

def _make_json_bytes(data: dict, bom: bool = False) -> bytes:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    raw = text.encode("utf-8")
    return (UTF8_BOM + raw) if bom else raw


class TestPatchJsonText:

    def test_lowercase_field_patched(self):
        raw = _make_json_bytes({"Шифр": 12, "ДопШифр": "frt", "Маршрут": "test"})
        patch = _patch_json_text(raw, "frt")
        assert patch is not None
        assert patch.old_value == "frt"
        assert patch.new_value == "FRT"
        assert '"ДопШифр": "FRT"' in patch.new_text
        assert patch.had_bom is False

    def test_already_uppercase_returns_none(self):
        raw = _make_json_bytes({"ДопШифр": "FRT"})
        result = _patch_json_text(raw, "FRT")
        assert result is None

    def test_bom_preserved(self):
        raw = _make_json_bytes({"ДопШифр": "frt"}, bom=True)
        patch = _patch_json_text(raw, "frt")
        assert patch is not None
        assert patch.had_bom is True

    def test_null_dopshifr_returns_none(self):
        raw = _make_json_bytes({"ДопШифр": None})
        result = _patch_json_text(raw, "")
        assert result is None

    def test_missing_dopshifr_returns_none(self):
        raw = _make_json_bytes({"Шифр": 1})
        result = _patch_json_text(raw, "frt")
        assert result is None

    def test_result_json_valid(self):
        raw = _make_json_bytes({"Шифр": 12, "ДопШифр": "abc", "extra": 42})
        patch = _patch_json_text(raw, "abc")
        assert patch is not None
        parsed = json.loads(patch.new_text)
        assert parsed["ДопШифр"] == "ABC"
        assert parsed["Шифр"] == 12
        assert parsed["extra"] == 42

    def test_mismatch_filename_vs_json_raises_value_error(self):
        # Поле в JSON не совпадает с ДопШифром из имени файла
        raw = _make_json_bytes({"ДопШифр": "abc"})
        with pytest.raises(ValueError, match="не совпадает"):
            _patch_json_text(raw, "xyz")

    def test_cyrillic_dop(self):
        raw = _make_json_bytes({"ДопШифр": "тлб"})
        patch = _patch_json_text(raw, "тлб")
        assert patch is not None
        assert patch.new_value == "ТЛБ"

    def test_field_not_modified_twice(self):
        # Если значение встречается в другом месте — безопасная проверка
        # В рамках нашего паттерна ищем именно "ДопШифр"\s*:\s*"frt",
        # так что дубль значения в другом поле не мешает
        data = {"ДопШифр": "frt", "Маршрут": "frt long route"}
        raw = _make_json_bytes(data)
        patch = _patch_json_text(raw, "frt")
        assert patch is not None
        result = json.loads(patch.new_text)
        assert result["ДопШифр"] == "FRT"
        # Маршрут не тронут
        assert result["Маршрут"] == "frt long route"

    def test_value_appears_twice_in_key_raises(self):
        # Создаём JSON вручную, где "ДопШифр": "frt" встречается дважды
        # (технически невалидный JSON с дублирующимся ключом — json.dumps не создаст такое,
        # поэтому строим текст вручную)
        raw = '{"ДопШифр": "frt", "ДопШифр": "frt"}'.encode("utf-8")
        with pytest.raises(ValueError, match="найден"):
            _patch_json_text(raw, "frt")
