# Version 1.2 - 16.06.2026 22:00:00 GMT
# Unit tests for services/seo/report_seo.py
# Описание: Проверяет чистые функции SEO-модуля:
#           parse_report_query, build_canonical_query, build_title, build_description,
#           _build_seo_head_html (OG-теги + размеры картинки),
#           build_route_html (microdata schema.org),
#           _extract_title_description (единый источник title/description из HTML).
#           Без БД и сервера — только чистая логика строк.
# 1.2: добавлены TestExtractTitleDescription; og:image:width/height в TestSeoHeadHtml.
# 1.1: добавлены TestSeoHeadHtml (OG + canonical) и TestBuildRouteHtmlMicrodata.

from __future__ import annotations

import unittest


# ---------------------------------------------------------------------------
# parse_report_query
# ---------------------------------------------------------------------------


class TestParseReportQuery(unittest.TestCase):
    def _parse(self, raw: str):
        from services.seo.report_seo import parse_report_query
        return parse_report_query(raw)

    def test_digits_only(self):
        result = self._parse("842")
        self.assertIsNotNone(result)
        shifr5, dop = result
        self.assertEqual(shifr5, "00842")
        self.assertEqual(dop, "")

    def test_shifr_with_dopshifr(self):
        result = self._parse("1-TST")
        self.assertIsNotNone(result)
        shifr5, dop = result
        self.assertEqual(shifr5, "00001")
        self.assertEqual(dop, "TST")

    def test_percent_encoded_dopshifr(self):
        # %D0%A2%D0%A1%D0%A1%D0%A0 = "ТССР" (кириллица)
        result = self._parse("1-%D0%A2%D0%A1%D0%A1%D0%A0")
        self.assertIsNotNone(result)
        shifr5, dop = result
        self.assertEqual(shifr5, "00001")
        self.assertEqual(dop, "ТССР")

    def test_empty_string_returns_none(self):
        self.assertIsNone(self._parse(""))

    def test_text_without_digits_returns_none(self):
        self.assertIsNone(self._parse("nodigits"))

    def test_shifr_padded_to_5_digits(self):
        result = self._parse("99")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "00099")

    def test_already_5_digit_unchanged(self):
        result = self._parse("12345")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "12345")

    def test_six_digit_shifr(self):
        result = self._parse("123456")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "123456")

    def test_none_input_returns_none(self):
        self.assertIsNone(self._parse(None))


# ---------------------------------------------------------------------------
# build_canonical_query
# ---------------------------------------------------------------------------


class TestBuildCanonicalQuery(unittest.TestCase):
    def _build(self, row: dict) -> str:
        from services.seo.report_seo import build_canonical_query
        return build_canonical_query(row)

    def test_shifr_only(self):
        self.assertEqual(self._build({"Шифр": 42, "ДопШифр": None}), "42")

    def test_shifr_with_dopshifr(self):
        self.assertEqual(self._build({"Шифр": 1, "ДопШифр": "TST"}), "1-TST")

    def test_leading_zeros_in_shifr_removed(self):
        # В БД Шифр хранится как integer — str(int("00001")) = "1"
        self.assertEqual(self._build({"Шифр": "00001", "ДопШифр": "TST"}), "1-TST")

    def test_empty_dopshifr_no_dash(self):
        self.assertEqual(self._build({"Шифр": 100, "ДопШифр": ""}), "100")

    def test_whitespace_dopshifr_stripped(self):
        self.assertEqual(self._build({"Шифр": 5, "ДопШифр": "  TST  "}), "5-TST")

    def test_legacy_lowercase_dopshifr_preserved(self):
        """ДопШифр берётся из БД как есть — legacy нижний регистр не меняется."""
        self.assertEqual(self._build({"Шифр": 1, "ДопШифр": "ш"}), "1-ш")


# ---------------------------------------------------------------------------
# build_title
# ---------------------------------------------------------------------------


class TestBuildTitle(unittest.TestCase):
    def _title(self, row: dict) -> str:
        from services.seo.report_seo import build_title
        return build_title(row)

    def _row(self, **kw) -> dict:
        base = {
            "Шифр": 1, "ДопШифр": "TST",
            "Тип": None, "КатегорияС": None, "КатегорияПо": None,
            "РайонОбщий": None, "Район": None, "Год": None,
        }
        base.update(kw)
        return base

    def test_starts_with_report_canonical(self):
        title = self._title(self._row())
        self.assertTrue(title.startswith("Отчёт 1-TST"))

    def test_includes_type(self):
        title = self._title(self._row(Тип="горный"))
        self.assertIn("горный", title)

    def test_includes_region(self):
        title = self._title(self._row(РайонОбщий="Кавказ"))
        self.assertIn("Кавказ", title)

    def test_includes_year(self):
        title = self._title(self._row(Год=2024))
        self.assertIn("2024", title)

    def test_minimal_row_no_crash(self):
        title = self._title({"Шифр": 42, "ДопШифр": None})
        self.assertIn("42", title)

    def test_category_with_type(self):
        title = self._title(self._row(Тип="горный", КатегорияС="3", КатегорияПо="4"))
        self.assertIn("к.с.", title)


# ---------------------------------------------------------------------------
# build_description
# ---------------------------------------------------------------------------


class TestBuildDescription(unittest.TestCase):
    def _desc(self, row: dict, max_len: int = 160) -> str:
        from services.seo.report_seo import build_description
        return build_description(row, max_len)

    def _row(self, **kw) -> dict:
        base = {
            "Шифр": 1, "ДопШифр": "TST",
            "Тип": None, "КатегорияС": None, "КатегорияПо": None,
            "РайонОбщий": None, "Район": None, "Год": None,
            "Автор": None, "Маршрут": None,
        }
        base.update(kw)
        return base

    def test_length_within_max(self):
        row = self._row(
            Тип="горный", КатегорияС="3", Год=2024,
            РайонОбщий="Кавказ", Автор="Иванов",
            Маршрут="Поход через перевал Клухор и обратно",
        )
        desc = self._desc(row, max_len=160)
        self.assertLessEqual(len(desc), 160)

    def test_includes_route_if_fits(self):
        row = self._row(Маршрут="Горный перевал")
        desc = self._desc(row, max_len=200)
        self.assertIn("Горный перевал", desc)

    def test_route_truncated_at_word_boundary(self):
        long_route = "очень длинный маршрут через несколько перевалов и долин"
        row = self._row(Маршрут=long_route)
        desc = self._desc(row, max_len=80)
        self.assertLessEqual(len(desc), 80)
        # Усечение по слову — не обрывается на середине
        if "…" in desc:
            self.assertNotEqual(desc[-2], " ")

    def test_starts_with_report_prefix(self):
        desc = self._desc(self._row())
        self.assertTrue(desc.startswith("Отчёт 1-TST"))


# ---------------------------------------------------------------------------
# _extract_title_description: единый источник title/description из HTML
# ---------------------------------------------------------------------------


class TestExtractTitleDescription(unittest.TestCase):
    def _extract(self, html_str: str) -> tuple:
        from services.seo.report_seo import _extract_title_description
        return _extract_title_description(html_str)

    def test_extracts_plain_title(self):
        t, d = self._extract("<title>Привет мир</title>")
        self.assertEqual(t, "Привет мир")
        self.assertEqual(d, "")

    def test_extracts_plain_description(self):
        t, d = self._extract('<meta name="description" content="Описание сайта">')
        self.assertEqual(t, "")
        self.assertEqual(d, "Описание сайта")

    def test_extracts_both(self):
        html_str = (
            '<title>Библиотека</title>\n'
            '<meta name="description" content="Крупнейшая библиотека">'
        )
        t, d = self._extract(html_str)
        self.assertEqual(t, "Библиотека")
        self.assertEqual(d, "Крупнейшая библиотека")

    def test_unescapes_html_entities(self):
        t, d = self._extract(
            '<title>Раздел &amp; подраздел</title>'
            '<meta name="description" content="Текст &lt;примера&gt;">'
        )
        self.assertEqual(t, "Раздел & подраздел")
        self.assertEqual(d, "Текст <примера>")

    def test_strips_whitespace(self):
        t, d = self._extract("<title>  Пробелы  </title>")
        self.assertEqual(t, "Пробелы")

    def test_missing_tags_return_empty_strings(self):
        t, d = self._extract("<html><body>Нет мета-тегов</body></html>")
        self.assertEqual(t, "")
        self.assertEqual(d, "")

    def test_works_with_seo_head_zone(self):
        """Корректно извлекает из шаблона с зоной SEO_HEAD."""
        html_str = (
            '<!--SEO_HEAD--><title>О проекте tLib</title>\n'
            '    <meta name="description" content="История библиотеки."><!--/SEO_HEAD-->'
        )
        t, d = self._extract(html_str)
        self.assertEqual(t, "О проекте tLib")
        self.assertEqual(d, "История библиотеки.")


# ---------------------------------------------------------------------------
# _build_seo_head_html: canonical + Open Graph для отчётов
# ---------------------------------------------------------------------------


class TestSeoHeadHtml(unittest.TestCase):
    def _head(self, row: dict, site_url: str = "https://tlib.ru") -> str:
        import services.seo.report_seo as m
        original = m.SITE_URL
        m.SITE_URL = site_url
        try:
            return m._build_seo_head_html(row)
        finally:
            m.SITE_URL = original

    def _row(self, **kw) -> dict:
        base = {
            "Шифр": 42, "ДопШифр": "TST",
            "Тип": "горный", "КатегорияС": "3", "КатегорияПо": "3",
            "РайонОбщий": "Кавказ", "Район": "Приэльбрусье",
            "Год": 2024, "МесяцС": 7, "МесяцПо": 8,
            "Автор": "Иванов", "Маршрут": "Через перевал Клухор",
        }
        base.update(kw)
        return base

    def test_canonical_tag_present(self):
        head = self._head(self._row())
        self.assertIn('rel="canonical"', head)
        self.assertIn("https://tlib.ru/?42-TST", head)

    def test_og_type_article(self):
        head = self._head(self._row())
        self.assertIn('property="og:type"', head)
        self.assertIn('content="article"', head)

    def test_og_title_matches_build_title(self):
        from services.seo.report_seo import build_title
        row = self._row()
        head = self._head(row)
        expected_title = build_title(row)
        self.assertIn(expected_title, head)

    def test_og_url_matches_canonical(self):
        head = self._head(self._row())
        self.assertIn('property="og:url"', head)
        self.assertIn("https://tlib.ru/?42-TST", head)

    def test_og_image_present(self):
        head = self._head(self._row())
        self.assertIn('property="og:image"', head)
        self.assertIn("og-image.png", head)

    def test_og_description_present(self):
        head = self._head(self._row())
        self.assertIn('property="og:description"', head)

    def test_og_image_width_present(self):
        head = self._head(self._row())
        self.assertIn('property="og:image:width"', head)
        self.assertIn('content="1200"', head)

    def test_og_image_height_present(self):
        head = self._head(self._row())
        self.assertIn('property="og:image:height"', head)
        self.assertIn('content="630"', head)

    def test_xss_in_marshrut_escaped(self):
        row = self._row(Маршрут='<script>alert("xss")</script>')
        head = self._head(row)
        self.assertNotIn("<script>", head)


# ---------------------------------------------------------------------------
# build_route_html: microdata schema.org/CreativeWork
# ---------------------------------------------------------------------------


class TestBuildRouteHtmlMicrodata(unittest.TestCase):
    def _html(self, **kw) -> str:
        from services.seo.report_seo import build_route_html
        base = {
            "Шифр": 1, "ДопШифр": "TST",
            "Маршрут": "Через перевал", "Тип": "горный",
            "ТипСудна": None, "КатегорияС": "3", "КатегорияПо": "3",
            "РайонОбщий": "Кавказ", "Район": "Приэльбрусье",
            "Год": 2024, "МесяцС": 7, "МесяцПо": 7,
            "Автор": "Иванов", "Город": "Москва", "Комментарии": None,
        }
        base.update(kw)
        return build_route_html(base)

    def test_container_has_itemscope(self):
        html_str = self._html()
        self.assertIn("itemscope", html_str)
        self.assertIn('itemtype="https://schema.org/CreativeWork"', html_str)

    def test_name_meta_present(self):
        html_str = self._html()
        self.assertIn('itemprop="name"', html_str)
        self.assertIn("Через перевал", html_str)

    def test_author_meta_present(self):
        html_str = self._html()
        self.assertIn('itemprop="author"', html_str)
        self.assertIn("Иванов", html_str)

    def test_date_created_meta_present(self):
        html_str = self._html()
        self.assertIn('itemprop="dateCreated"', html_str)
        self.assertIn("2024", html_str)

    def test_spatial_coverage_meta_present(self):
        html_str = self._html()
        self.assertIn('itemprop="spatialCoverage"', html_str)

    def test_no_description_meta_when_comments_empty(self):
        html_str = self._html(Комментарии=None)
        self.assertNotIn('itemprop="description"', html_str)

    def test_description_meta_when_comments_present(self):
        html_str = self._html(Комментарии="Сложный маршрут")
        self.assertIn('itemprop="description"', html_str)
        self.assertIn("Сложный маршрут", html_str)

    def test_no_author_meta_when_empty(self):
        html_str = self._html(Автор=None)
        self.assertNotIn('itemprop="author"', html_str)

    def test_name_fallback_to_canonical_when_no_marshrut(self):
        html_str = self._html(Маршрут=None)
        self.assertIn('itemprop="name"', html_str)
        self.assertIn("Отчёт", html_str)

    def test_xss_in_comments_escaped(self):
        html_str = self._html(Комментарии='<script>alert(1)</script>')
        self.assertNotIn("<script>", html_str)


if __name__ == "__main__":
    unittest.main()
