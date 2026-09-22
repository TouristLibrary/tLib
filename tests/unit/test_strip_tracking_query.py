# Version 1.1 - 22.09.2026 11:05:00 GMT
# Unit tests for routers/static_router.py::_strip_tracking_query
# Описание: Проверяет вырезание меток ROBOTS_CLEAN_PARAMS из сырой query
#           и снятие хвостового «=» у компактного шифра (3725= от Яндекса)
#           без перекодирования оставшихся сегментов.
#           None — вырезать нечего, повторный проход редиректа не даёт.
# 1.1: 3725= без меток и 3725=&ysclid=… → 3725; допшифр с «=» сохраняет кодировку.

from routers.static_router import _strip_tracking_query


def test_ysclid_stripped_cipher_kept():
    assert _strip_tracking_query("3725&ysclid=mubm5byt6f260129368") == "3725"


def test_dopshifr_encoding_preserved():
    raw = "3725-%D0%A2%D0%A1%D0%A1%D0%A0&ysclid=TOKEN"
    assert _strip_tracking_query(raw) == "3725-%D0%A2%D0%A1%D0%A1%D0%A0"


def test_named_filter_kept():
    assert _strip_tracking_query("Шифр=3725&ysclid=TOKEN") == "Шифр=3725"


def test_only_ysclid_becomes_empty():
    assert _strip_tracking_query("ysclid=TOKEN") == ""


def test_clean_cipher_unchanged():
    """Повторный заход на /?3725 после редиректа не даёт новый редирект."""
    assert _strip_tracking_query("3725") is None


def test_filters_without_tracking_unchanged():
    assert _strip_tracking_query("Шифр=3725&Тип=лыжный") is None


def test_utm_stripped():
    assert _strip_tracking_query("3725&utm_source=yandex") == "3725"


def test_key_case_ignored():
    assert _strip_tracking_query("3725&YSCLID=TOKEN") == "3725"


def test_empty_query():
    assert _strip_tracking_query("") is None


def test_trailing_equals_without_tracking():
    assert _strip_tracking_query("3725=") == "3725"


def test_trailing_equals_with_ysclid():
    assert _strip_tracking_query("3725=&ysclid=TOKEN") == "3725"


def test_dopshifr_trailing_equals_keeps_encoding():
    raw = "3725-%D0%A2%D0%A1%D0%A1%D0%A0=&ysclid=TOKEN"
    assert _strip_tracking_query(raw) == "3725-%D0%A2%D0%A1%D0%A1%D0%A0"


def test_notfound_unchanged():
    assert _strip_tracking_query("notfound=1") is None
