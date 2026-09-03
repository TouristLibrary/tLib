# Version 1.0 - 10.07.2026 09:45:00 GMT
# Тесты для services/hidden_reports.py
# Описание: Проверяет разбор/нормализацию списка «Шифр-ДопШифр» из textarea (parse_and_normalize)
#           и формирование текста для сохранения/отображения (format_for_storage).

from services.hidden_reports import format_for_storage, parse_and_normalize


class TestParseAndNormalize:
    def test_empty_text_returns_empty_lists(self):
        assert parse_and_normalize("") == ([], [])

    def test_whitespace_only_returns_empty_lists(self):
        assert parse_and_normalize("   \n  ") == ([], [])

    def test_single_token_without_dopshifr(self):
        ids, invalid = parse_and_normalize("345")
        assert ids == ["00345"]
        assert invalid == []

    def test_single_token_with_dopshifr_normalizes_case(self):
        ids, invalid = parse_and_normalize("12-tlib")
        assert ids == ["00012-TLIB"]
        assert invalid == []

    def test_semicolon_separator(self):
        ids, invalid = parse_and_normalize("12-TLIB;345")
        assert ids == ["00012-TLIB", "00345"]
        assert invalid == []

    def test_comma_separator(self):
        ids, invalid = parse_and_normalize("12-TLIB,345")
        assert sorted(ids) == ["00012-TLIB", "00345"]
        assert invalid == []

    def test_whitespace_and_newline_separators(self):
        ids, invalid = parse_and_normalize("12-TLIB 345\n00099-FRT")
        assert sorted(ids) == ["00012-TLIB", "00099-FRT", "00345"]
        assert invalid == []

    def test_duplicates_after_normalization_are_deduplicated(self):
        ids, invalid = parse_and_normalize("12-tlib 00012-TLIB 12-TLIB")
        assert ids == ["00012-TLIB"]
        assert invalid == []

    def test_non_digit_shifr_is_invalid(self):
        ids, invalid = parse_and_normalize("abc-TLIB")
        assert ids == []
        assert invalid == ["abc-TLIB"]

    def test_invalid_dopshifr_too_long_is_invalid(self):
        ids, invalid = parse_and_normalize("12-TOOLONG")
        assert ids == []
        assert invalid == ["12-TOOLONG"]

    def test_mixed_valid_and_invalid_tokens(self):
        ids, invalid = parse_and_normalize("12-TLIB; abc-TLIB; 345")
        assert sorted(ids) == ["00012-TLIB", "00345"]
        assert invalid == ["abc-TLIB"]

    def test_result_is_sorted(self):
        ids, _invalid = parse_and_normalize("345 00012-TLIB 00099")
        assert ids == sorted(ids)


class TestFormatForStorage:
    def test_empty_list_returns_empty_string(self):
        assert format_for_storage([]) == ""

    def test_joins_with_newline_sorted(self):
        assert format_for_storage(["00345", "00012-TLIB"]) == "00012-TLIB\n00345"

    def test_deduplicates(self):
        assert format_for_storage(["00012-TLIB", "00012-TLIB"]) == "00012-TLIB"
