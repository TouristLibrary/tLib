# Version 1.0 - 15.06.2026 10:18:00 GMT
# Тесты для services/id_utils.py
# Описание: Проверяет контракты всех четырёх публичных функций модуля нормализации ID:
#           normalize_shifr_to_5digits, normalize_dopshifr, make_norm_id, normalize_group_id.

import pytest

from services.id_utils import (
    make_norm_id,
    normalize_dopshifr,
    normalize_group_id,
    normalize_shifr_to_5digits,
)


class TestNormalizeShifrTo5Digits:
    def test_short_int(self):
        assert normalize_shifr_to_5digits(12) == "00012"

    def test_three_digits(self):
        assert normalize_shifr_to_5digits(345) == "00345"

    def test_exactly_five(self):
        assert normalize_shifr_to_5digits(12345) == "12345"

    def test_more_than_five(self):
        assert normalize_shifr_to_5digits(123456) == "123456"

    def test_string_input(self):
        assert normalize_shifr_to_5digits("42") == "00042"

    def test_zero(self):
        assert normalize_shifr_to_5digits(0) == "00000"

    def test_non_numeric_raises_value_error(self):
        with pytest.raises((ValueError, TypeError)):
            normalize_shifr_to_5digits("abc")

    def test_none_raises(self):
        with pytest.raises((ValueError, TypeError)):
            normalize_shifr_to_5digits(None)


class TestNormalizeDopshifr:
    def test_lowercase_to_upper(self):
        assert normalize_dopshifr("tlib") == "TLIB"

    def test_already_upper(self):
        assert normalize_dopshifr("TST") == "TST"

    def test_strip_spaces(self):
        assert normalize_dopshifr("  tst  ") == "TST"

    def test_empty_string(self):
        assert normalize_dopshifr("") == ""

    def test_none_returns_empty(self):
        assert normalize_dopshifr(None) == ""

    def test_mixed_case(self):
        assert normalize_dopshifr("TlIb") == "TLIB"


class TestMakeNormId:
    def test_with_dopshifr(self):
        assert make_norm_id(12, "tlib") == "00012-TLIB"

    def test_without_dopshifr_empty_string(self):
        assert make_norm_id(345, "") == "00345"

    def test_without_dopshifr_none(self):
        assert make_norm_id(1, None) == "00001"

    def test_dopshifr_uppercase(self):
        assert make_norm_id(1, "frt") == "00001-FRT"

    def test_five_digit_shifr(self):
        assert make_norm_id(12345, "А") == "12345-А"

    def test_string_shifr(self):
        assert make_norm_id("42", "TST") == "00042-TST"

    def test_invalid_shifr_raises(self):
        with pytest.raises((ValueError, TypeError)):
            make_norm_id("abc", "TST")

    def test_idempotent_already_normalized(self):
        # Нормализация идемпотентна: нормализованный вход не меняется
        assert make_norm_id("00012", "TLIB") == "00012-TLIB"


class TestNormalizeGroupId:
    def test_with_dopshifr_lower(self):
        assert normalize_group_id("12-frt") == "00012-FRT"

    def test_with_dopshifr_upper(self):
        assert normalize_group_id("12-FRT") == "00012-FRT"

    def test_without_dopshifr(self):
        assert normalize_group_id("345") == "00345"

    def test_already_normalized(self):
        assert normalize_group_id("00012-FRT") == "00012-FRT"

    def test_single_digit_shifr(self):
        assert normalize_group_id("1-TST") == "00001-TST"

    def test_unrecognized_returns_original(self):
        assert normalize_group_id("abc") == "abc"

    def test_empty_dopshifr_after_dash(self):
        # "123-" — ДопШифр пустой; результат без дефиса
        assert normalize_group_id("123-") == "00123"

    def test_idempotent(self):
        gid = "00012-FRT"
        assert normalize_group_id(normalize_group_id(gid)) == normalize_group_id(gid)
