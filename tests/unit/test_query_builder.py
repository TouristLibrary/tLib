# Version 1.0 - 12.06.2026 18:00:00 GMT
# Unit tests for services/database/query_builder.py, query_filters.py, query_helpers.py
# Описание: Проверяет построение SQL-запросов и вспомогательные функции слоя поиска.
#           Тесты чистой логики: без БД, без сервера. Используется реальный query builder
#           с патчем DATABASE_TABLE_NAME.

from __future__ import annotations

import unittest
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _build(form_data: dict, kategoria_list=None) -> tuple[str, list]:
    """Строит запрос через build_search_query с патчем таблицы."""
    from services.database.query_builder import build_search_query
    return build_search_query(form_data, kategoria_list)


# ---------------------------------------------------------------------------
# get_form_value
# ---------------------------------------------------------------------------


class TestGetFormValue(unittest.TestCase):
    def _get(self, key, data):
        from services.database.query_helpers import get_form_value
        return get_form_value(data, key)

    def test_present_string_stripped(self):
        self.assertEqual(self._get("Шифр", {"Шифр": "  42  "}), "42")

    def test_absent_key_returns_empty(self):
        self.assertEqual(self._get("Шифр", {}), "")

    def test_none_value_returns_empty(self):
        self.assertEqual(self._get("Шифр", {"Шифр": None}), "")

    def test_int_value_coerced_to_str(self):
        self.assertEqual(self._get("Шифр", {"Шифр": 99}), "99")


# ---------------------------------------------------------------------------
# extract_words_from_text
# ---------------------------------------------------------------------------


class TestExtractWords(unittest.TestCase):
    def _words(self, text):
        from services.database.query_helpers import extract_words_from_text
        return extract_words_from_text(text)

    def test_simple_cyrillic(self):
        self.assertEqual(self._words("Горный маршрут"), ["Горный", "маршрут"])

    def test_punctuation_stripped(self):
        self.assertEqual(self._words("тест, проверка!"), ["тест", "проверка"])

    def test_wildcard_preserved(self):
        words = self._words("тест%звук")
        self.assertIn("тест%звук", words)

    def test_empty_returns_empty_list(self):
        self.assertEqual(self._words(""), [])
        self.assertEqual(self._words(None), [])

    def test_dashes_preserved(self):
        words = self._words("Карелия-Ладога")
        self.assertIn("Карелия-Ладога", words)


# ---------------------------------------------------------------------------
# parse_route_field
# ---------------------------------------------------------------------------


class TestParseRouteField(unittest.TestCase):
    def _parse(self, value):
        from services.database.query_helpers import parse_route_field
        return parse_route_field(value)

    def test_shifr_only(self):
        result = self._parse("123 горный маршрут")
        self.assertEqual(result["shifr"], "123")
        self.assertIsNone(result["dopshifr"])
        self.assertEqual(result["route"], "горный маршрут")

    def test_shifr_with_dopshifr(self):
        result = self._parse("42-TST горный")
        self.assertEqual(result["shifr"], "42")
        self.assertEqual(result["dopshifr"], "TST")
        self.assertEqual(result["route"], "горный")

    def test_text_only(self):
        result = self._parse("горный маршрут без шифра")
        self.assertIsNone(result["shifr"])
        self.assertIsNone(result["dopshifr"])
        self.assertIn("горный", result["route"])

    def test_empty_returns_empty(self):
        result = self._parse("")
        self.assertIsNone(result["shifr"])
        self.assertEqual(result["route"], "")

    def test_shifr_without_text(self):
        result = self._parse("100-TLIB")
        self.assertEqual(result["shifr"], "100")
        self.assertEqual(result["dopshifr"], "TLIB")
        self.assertEqual(result["route"], "")


# ---------------------------------------------------------------------------
# category_sort_key
# ---------------------------------------------------------------------------


class TestCategorySortKey(unittest.TestCase):
    def _key(self, cat):
        from services.database.query_helpers import category_sort_key
        return category_sort_key(cat)

    def test_nk_is_simplest(self):
        self.assertLess(self._key("н/к"), self._key("1"))

    def test_bk_after_nk(self):
        self.assertLess(self._key("н/к"), self._key("б/к"))
        self.assertLess(self._key("б/к"), self._key("1"))

    def test_numeric_order(self):
        self.assertLess(self._key("1"), self._key("2"))
        self.assertLess(self._key("2"), self._key("3"))
        self.assertLess(self._key("5"), self._key("6"))

    def test_elements_5_before_5(self):
        self.assertLess(self._key("с элементами 5 к.с."), self._key("5"))

    def test_case_insensitive(self):
        self.assertEqual(self._key("Н/К"), self._key("н/к"))


# ---------------------------------------------------------------------------
# build_search_query — сборка SQL
# ---------------------------------------------------------------------------


class TestBuildSearchQueryBasic(unittest.TestCase):
    def test_empty_form_returns_select_all(self):
        query, params = _build({})
        self.assertIn("SELECT *", query)
        self.assertIn("WHERE 1=1", query)
        self.assertEqual(params, [])

    def test_shifr_adds_exact_filter(self):
        query, params = _build({"Шифр": "42"})
        self.assertIn("Шифр = ?", query)
        # get_form_value возвращает строку — params содержит строку
        self.assertIn("42", params)

    def test_dopshifr_regular_adds_lower_filter(self):
        query, params = _build({"ДопШифр": "TST"})
        self.assertIn("LOWER(ДопШифр) = LOWER(?)", query)
        self.assertIn("TST", params)

    def test_dopshifr_net_adds_null_filter(self):
        query, params = _build({"ДопШифр": "нет"})
        self.assertIn("ДопШифр IS NULL", query)
        self.assertIn("TRIM(ДопШифр) = ''", query)
        self.assertNotIn("нет", params)

    def test_raion_obshiy_adds_lower_filter(self):
        query, params = _build({"РайонОбщий": "Кавказ"})
        self.assertIn("LOWER(РайонОбщий) = LOWER(?)", query)
        self.assertIn("Кавказ", params)

    def test_tip_adds_filter(self):
        query, params = _build({"Тип": "горный"})
        self.assertIn("LOWER(Тип) = LOWER(?)", query)
        self.assertIn("горный", params)


class TestBuildSearchQuerySorting(unittest.TestCase):
    def test_default_sort_is_god_desc(self):
        query, _ = _build({})
        self.assertIn("ORDER BY", query)
        self.assertIn("Год", query)
        self.assertIn("DESC", query)

    def test_sort_by_kategoria(self):
        query, _ = _build({"sortColumn": "Категория", "sortOrder": "asc"})
        self.assertIn("CATEGORY_INDEX", query)
        self.assertIn("ASC", query)

    def test_sort_by_data_zagruzki(self):
        query, _ = _build({"sortColumn": "ДатаВремяЗагрузки", "sortOrder": "desc"})
        self.assertIn("ДатаВремяЗагрузки", query)
        self.assertIn("DESC", query)

    def test_invalid_sort_column_falls_back_to_default(self):
        query, _ = _build({"sortColumn": "INJECTION; DROP TABLE", "sortOrder": "asc"})
        self.assertIn("Год", query)
        self.assertNotIn("INJECTION", query)

    def test_invalid_sort_order_falls_back_to_desc(self):
        query, _ = _build({"sortOrder": "sideways"})
        self.assertIn("DESC", query)
        self.assertNotIn("sideways", query)


class TestBuildSearchQueryTextFilters(unittest.TestCase):
    def test_marshrut_word_filter(self):
        query, params = _build({"Маршрут": "горный перевал"})
        self.assertIn("LOWER(Маршрут) LIKE LOWER(?)", query)
        self.assertTrue(any("горный" in str(p) for p in params))
        self.assertTrue(any("перевал" in str(p) for p in params))

    def test_avtor_word_filter(self):
        query, params = _build({"Автор": "Иванов Петр"})
        self.assertIn("LOWER(Автор) LIKE LOWER(?)", query)
        self.assertTrue(any("%Иванов%" in str(p) for p in params))
        self.assertTrue(any("%Петр%" in str(p) for p in params))

    def test_raion_searches_both_fields(self):
        query, params = _build({"Район": "Карелия"})
        self.assertIn("LOWER(Район) LIKE LOWER(?)", query)
        self.assertIn("LOWER(РайонОбщий) LIKE LOWER(?)", query)

    def test_marshrut_with_code_extracts_shifr(self):
        query, params = _build({"Маршрут": "42-TST"})
        self.assertIn("Шифр = ?", query)
        self.assertIn("LOWER(ДопШифр) = LOWER(?)", query)


class TestBuildSearchQueryDateFilters(unittest.TestCase):
    def test_god_from_adds_filter(self):
        query, params = _build({"ГодС": "2010"})
        self.assertIn("Год >= ?", query)
        self.assertIn(2010, params)  # build_date_filters явно приводит к int

    def test_god_to_adds_filter(self):
        query, params = _build({"ГодПо": "2020"})
        self.assertIn("Год <= ?", query)
        self.assertIn(2020, params)

    def test_god_range(self):
        query, params = _build({"ГодС": "2015", "ГодПо": "2020"})
        self.assertIn("Год >= ?", query)
        self.assertIn("Год <= ?", query)
        self.assertIn(2015, params)
        self.assertIn(2020, params)

    def test_month_normal_interval(self):
        query, params = _build({"МесяцС": "3", "МесяцПо": "6"})
        self.assertIn("МесяцС", query)
        self.assertIn(3, params)  # build_date_filters явно приводит к int
        self.assertIn(6, params)

    def test_month_cyclic_interval(self):
        # декабрь-май = 12-5 → cyclic
        query, params = _build({"МесяцС": "12", "МесяцПо": "5"})
        self.assertIn("МесяцС", query)
        self.assertIn(12, params)
        self.assertIn(5, params)

    def test_zagruzhenoc_filter(self):
        query, params = _build({"ЗагруженоС": "2024-01-01"})
        self.assertIn("ДатаВремяЗагрузки >= ?", query)
        self.assertTrue(any("2024-01-01" in str(p) for p in params))


class TestBuildSearchQueryCategories(unittest.TestCase):
    _CATS = ["н/к", "б/к", "1", "2", "3", "4", "5", "6"]

    def test_category_range_adds_in_filter(self):
        query, params = _build(
            {"КатегорияС": "1", "КатегорияПо": "3"},
            kategoria_list=self._CATS,
        )
        self.assertIn("IN (", query)
        self.assertIn("1", params)
        self.assertIn("2", params)
        self.assertIn("3", params)

    def test_category_from_only_uses_max(self):
        query, params = _build({"КатегорияС": "3"}, kategoria_list=self._CATS)
        self.assertIn("IN (", query)
        self.assertIn("3", params)
        self.assertIn("6", params)

    def test_category_inverted_range_adds_1_equals_0(self):
        # from > to → пустой интервал → 1=0
        query, params = _build(
            {"КатегорияС": "5", "КатегорияПо": "1"},
            kategoria_list=self._CATS,
        )
        self.assertIn("1=0", query)
        # В params не должно быть лишних значений категорий
        category_params = [p for p in params if p in self._CATS]
        self.assertEqual(category_params, [])

    def test_no_kategoria_list_skips_filter(self):
        query, params = _build({"КатегорияС": "3"}, kategoria_list=None)
        self.assertNotIn("CATEGORY_INDEX", query)
        self.assertNotIn("IN (", query)


if __name__ == "__main__":
    unittest.main()
