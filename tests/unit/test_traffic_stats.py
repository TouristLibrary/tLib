# Version 1.0 - 12.06.2026 20:00:00 GMT
# Unit tests for middlewares/traffic_stats.py
# Описание: Проверяет функции категоризации запросов (_categorize, _report_id_from_path,
#           _report_id_from_page) и StatsCollector (record, flush, query).
#           StatsCollector принимает db_path параметром — патчинг config не нужен.
#           flush() — async, запускается через asyncio.run().

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Тесты _categorize
# ---------------------------------------------------------------------------


class TestCategorize:
    def _cat(self, path: str):
        from middlewares.traffic_stats import _categorize
        return _categorize(path)

    def test_search(self):
        assert self._cat("/api/search") == "search"

    def test_report_page_with_dopshifr(self):
        assert self._cat("/?12345-ABC") == "report"

    def test_report_page_numeric_only(self):
        assert self._cat("/?12345") == "report"

    def test_download(self):
        # LOCAL_ARCHIVE_PATH = "/data"
        assert self._cat("/data/12345-TST.zip") == "download"

    def test_root_is_page(self):
        assert self._cat("/") == "page"

    def test_admin_is_page(self):
        assert self._cat("/admin") == "page"

    def test_api_other(self):
        assert self._cat("/api/health") == "api"
        assert self._cat("/api/reports-count") == "api"

    def test_js_static_is_none(self):
        assert self._cat("/js/app.js") is None
        assert self._cat("/css/style.css") is None

    def test_assets_static_is_none(self):
        assert self._cat("/assets/schema.json") is None

    def test_favicon_is_none(self):
        assert self._cat("/favicon.ico") is None

    def test_report_page_with_cyrillic_dopshifr(self):
        assert self._cat("/?1-ТССР") == "report"

    def test_invalid_query_is_none(self):
        # текст без цифр в начале — не отчёт
        assert self._cat("/?abc") is None


# ---------------------------------------------------------------------------
# Тесты _report_id_from_path
# ---------------------------------------------------------------------------


class TestReportIdFromPath:
    def _id(self, path: str):
        from middlewares.traffic_stats import _report_id_from_path
        return _report_id_from_path(path)

    def test_zip_with_dopshifr(self):
        assert self._id("/data/12345-TSSR.zip") == "12345-TSSR"

    def test_pdf_numeric_only(self):
        assert self._id("/data/12345.pdf") == "12345"

    def test_unrecognized_filename_is_none(self):
        assert self._id("/data/readme.txt") is None

    def test_no_path_separator(self):
        assert self._id("12345-TST.zip") == "12345-TST"


# ---------------------------------------------------------------------------
# Тесты _report_id_from_page
# ---------------------------------------------------------------------------


class TestReportIdFromPage:
    def _id(self, path: str):
        from middlewares.traffic_stats import _report_id_from_page
        return _report_id_from_page(path)

    def test_with_dopshifr(self):
        assert self._id("/?12345-ABC") == "12345-ABC"

    def test_numeric_only(self):
        assert self._id("/?12345") == "12345"

    def test_text_without_digits_is_none(self):
        assert self._id("/?abc") is None

    def test_no_question_mark_is_none(self):
        assert self._id("/12345") is None


# ---------------------------------------------------------------------------
# Тесты StatsCollector
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _collector(db_path: Path):
    from middlewares.traffic_stats import StatsCollector
    return StatsCollector(str(db_path), retention_days=30)


class TestStatsCollectorRecord:
    def test_download_increments_popular(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/data/00001-TST.zip", "1.2.3.4", 200, _now())
        assert c._popular[_now().strftime("%Y-%m-%d")]["00001-TST"] == 1

    def test_report_page_adds_ip_to_views(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/?42-TST", "1.2.3.4", 200, _now())
        today = _now().strftime("%Y-%m-%d")
        assert "1.2.3.4" in c._report_views[today]["42-TST"]

    def test_4xx_counted_in_errors(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/api/health", "1.2.3.4", 404, _now())
        hour = _now().strftime("%Y-%m-%dT%H")
        assert c._hourly_errors[hour]["4xx"] == 1

    def test_5xx_counted_in_errors(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/api/health", "1.2.3.4", 500, _now())
        hour = _now().strftime("%Y-%m-%dT%H")
        assert c._hourly_errors[hour]["5xx"] == 1

    def test_static_path_ignored(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/js/app.js", "1.2.3.4", 200, _now())
        # Ничего не записалось
        assert not any(c._hourly_hits.values())
        assert not any(c._daily_ips.values())

    def test_unique_ips_counted_per_day(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/api/search", "1.1.1.1", 200, _now())
        c.record("/api/search", "2.2.2.2", 200, _now())
        c.record("/api/search", "1.1.1.1", 200, _now())  # дубль
        today = _now().strftime("%Y-%m-%d")
        assert len(c._daily_ips[today]) == 2


class TestStatsCollectorFlush:
    def test_flush_writes_to_db(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/api/search", "1.2.3.4", 200, _now())
        c.record("/data/00001-TST.zip", "1.2.3.4", 200, _now())

        asyncio.run(c.flush())

        conn = sqlite3.connect(str(tmp_path / "stats.db"))
        hourly = conn.execute("SELECT SUM(hits) FROM hourly_stats").fetchone()[0]
        popular = conn.execute("SELECT COUNT(*) FROM daily_popular").fetchone()[0]
        daily_ips = conn.execute("SELECT COUNT(*) FROM daily_ips").fetchone()[0]
        conn.close()

        assert hourly and hourly >= 2
        assert popular >= 1
        assert daily_ips >= 1

    def test_flush_clears_in_memory(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/api/search", "1.2.3.4", 200, _now())
        asyncio.run(c.flush())

        # После flush in-memory счётчики обнулены
        assert not any(c._hourly_hits.values())
        assert not any(c._popular.values())

    def test_double_flush_no_duplication(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/api/search", "1.2.3.4", 200, _now())
        asyncio.run(c.flush())
        asyncio.run(c.flush())  # второй flush — данных нет, дублей не добавляет

        conn = sqlite3.connect(str(tmp_path / "stats.db"))
        total = conn.execute("SELECT SUM(hits) FROM hourly_stats WHERE category='search'").fetchone()[0]
        conn.close()
        assert total == 1


class TestStatsCollectorQuery:
    def test_query_returns_required_keys(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        result = c.query()
        assert "h24" in result
        assert "d7" in result
        assert "d30" in result
        assert "top_reports" in result

    def test_in_memory_data_visible_without_flush(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/api/search", "1.2.3.4", 200, _now())
        result = c.query()
        assert result["h24"]["search"] >= 1

    def test_after_flush_data_in_query(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/data/00001-TST.zip", "1.2.3.4", 200, _now())
        asyncio.run(c.flush())
        result = c.query()
        assert result["h24"]["download"] >= 1
        assert any(r["report_id"] == "00001-TST" for r in result["top_reports"])
