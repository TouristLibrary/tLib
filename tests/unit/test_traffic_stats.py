# Unit tests for middlewares/traffic_stats.py
# Описание: Проверяет категоризацию запросов и StatsCollector: маячок жеста делает IP
#           человеком, хиты и топы считаются только по таким IP и только после flush.
#           StatsCollector принимает db_path параметром — патчинг config не нужен.
#           flush() — async, запускается через asyncio.run().

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone


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

    def test_static_is_other(self):
        assert self._cat("/js/app.js") == "other"
        assert self._cat("/css/style.css") == "other"
        assert self._cat("/assets/schema.json") == "other"
        assert self._cat("/favicon.ico") == "other"

    def test_scanner_path_is_other(self):
        assert self._cat("/doc.aspx") == "other"

    def test_gesture(self):
        assert self._cat("/api/gesture") == "gesture"

    def test_search_with_query_is_api(self):
        assert self._cat("/api/search?page=1") == "api"
        assert self._cat("/api/search?auto=1") == "api"

    def test_report_page_with_cyrillic_dopshifr(self):
        assert self._cat("/?1-ТССР") == "report"

    def test_invalid_query_is_other(self):
        assert self._cat("/?abc") == "other"


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


def _gesture(c, ip: str, dt: datetime) -> None:
    c.record("/api/gesture", ip, 200, dt)


class TestStatsCollectorRecord:
    def test_4xx_counted_in_errors_but_not_as_hit(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/api/health", "1.2.3.4", 404, _now())
        hour = _now().strftime("%Y-%m-%dT%H")
        assert c._hourly_errors[hour]["4xx"] == 1
        assert c._hits[(hour, "1.2.3.4", "other", "")] == 1
        assert not any(key[2] == "api" for key in c._hits)

    def test_5xx_counted_in_errors(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/api/health", "1.2.3.4", 500, _now())
        hour = _now().strftime("%Y-%m-%dT%H")
        assert c._hourly_errors[hour]["5xx"] == 1

    def test_partial_download_is_not_a_hit(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/data/00001-TST.zip", "1.2.3.4", 206, _now())
        hour = _now().strftime("%Y-%m-%dT%H")
        assert c._hits[(hour, "1.2.3.4", "other", "")] == 1
        assert not any(key[2] == "download" for key in c._hits)

    def test_static_marks_ip_without_error(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/js/app.js", "1.2.3.4", 404, _now())
        hour = _now().strftime("%Y-%m-%dT%H")
        assert c._hits[(hour, "1.2.3.4", "other", "")] == 1
        assert not c._hourly_errors[hour]["4xx"]

    def test_repeated_request_increments_same_key(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/api/search", "1.1.1.1", 200, _now())
        c.record("/api/search", "1.1.1.1", 200, _now())
        hour = _now().strftime("%Y-%m-%dT%H")
        assert c._hits[(hour, "1.1.1.1", "search", "")] == 2


class TestStatsCollectorFlush:
    def test_flush_writes_hits(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/api/search", "1.2.3.4", 200, _now())
        c.record("/data/00001-TST.zip", "1.2.3.4", 200, _now())
        asyncio.run(c.flush())

        conn = sqlite3.connect(str(tmp_path / "stats.db"))
        total = conn.execute("SELECT SUM(hits) FROM hits").fetchone()[0]
        download = conn.execute(
            "SELECT report_id FROM hits WHERE category = 'download'"
        ).fetchone()[0]
        conn.close()
        assert total == 2
        assert download == "00001-TST"

    def test_flush_clears_in_memory(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/api/search", "1.2.3.4", 200, _now())
        asyncio.run(c.flush())
        assert not c._hits

    def test_double_flush_no_duplication(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/api/search", "1.2.3.4", 200, _now())
        asyncio.run(c.flush())
        asyncio.run(c.flush())

        conn = sqlite3.connect(str(tmp_path / "stats.db"))
        total = conn.execute(
            "SELECT SUM(hits) FROM hits WHERE category = 'search'"
        ).fetchone()[0]
        conn.close()
        assert total == 1

    def test_old_tables_are_dropped(self, tmp_path):
        db_path = tmp_path / "stats.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE daily_ips (date TEXT NOT NULL, ip TEXT NOT NULL, PRIMARY KEY (date, ip))"
        )
        conn.execute(
            "CREATE TABLE hourly_ips (hour TEXT NOT NULL, ip TEXT NOT NULL, bot INTEGER NOT NULL, "
            "PRIMARY KEY (hour, ip))"
        )
        conn.execute(
            "CREATE TABLE hourly_stats (hour TEXT NOT NULL, category TEXT NOT NULL, "
            "hits INTEGER, errors_4xx INTEGER, errors_5xx INTEGER, PRIMARY KEY (hour, category))"
        )
        conn.execute(
            "INSERT INTO hourly_stats VALUES ('2026-01-01T00', 'search', 5, 0, 0)"
        )
        conn.execute(
            "INSERT INTO hourly_stats VALUES ('2026-01-01T00', '_errors', 0, 2, 0)"
        )
        conn.commit()
        conn.close()

        _collector(db_path)

        conn = sqlite3.connect(str(db_path))
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        left = conn.execute(
            "SELECT category FROM hourly_stats"
        ).fetchall()
        conn.close()
        assert "daily_ips" not in tables
        assert "hourly_ips" not in tables
        assert "hits" in tables
        assert left == [("_errors",)]

    def test_retention_drops_old_rows(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        now = _now()
        c.record("/api/search", "1.1.1.1", 200, now - timedelta(days=40))
        c.record("/api/search", "2.2.2.2", 200, now - timedelta(days=10))
        asyncio.run(c.flush())

        conn = sqlite3.connect(str(tmp_path / "stats.db"))
        ips = {row[0] for row in conn.execute("SELECT ip FROM hits")}
        conn.close()
        assert ips == {"2.2.2.2"}


class TestStatsCollectorQuery:
    def test_query_returns_required_keys(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        result = c.query()
        assert result["since"] is None
        for period in ("h24", "d7", "d30"):
            assert "unique_ips" in result[period]
            assert "bot_ips" in result[period]
            assert "top_reports" in result[period]
            assert "top_report_views" in result[period]
            assert "errors_4xx" in result[period]

    def test_memory_is_invisible_until_flush(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/api/search", "1.2.3.4", 200, _now())
        _gesture(c, "1.2.3.4", _now())
        assert c.query()["h24"]["search"] == 0

    def test_human_is_ip_with_gesture(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        now = _now()
        c.record("/api/search", "1.1.1.1", 200, now)
        _gesture(c, "1.1.1.1", now)
        c.record("/api/search", "8.8.8.8", 200, now)
        c.record("/js/app.js", "9.9.9.9", 200, now)
        asyncio.run(c.flush())

        result = c.query()
        assert result["h24"]["unique_ips"] == 1
        assert result["h24"]["bot_ips"] == 2
        assert result["h24"]["search"] == 1
        assert result["since"] == now.strftime("%Y-%m-%dT%H")

    def test_view_before_gesture_counts(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        now = _now()
        c.record("/?42-TST", "1.1.1.1", 200, now)
        _gesture(c, "1.1.1.1", now)
        asyncio.run(c.flush())

        result = c.query()
        assert result["h24"]["report"] == 1
        assert result["d7"]["top_report_views"] == [
            {"report_id": "42-TST", "unique_ips": 1}
        ]

    def test_download_top_only_for_humans(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        now = _now()
        c.record("/data/00001-TST.zip", "1.1.1.1", 200, now)
        _gesture(c, "1.1.1.1", now)
        c.record("/data/00002-TST.zip", "8.8.8.8", 200, now)
        asyncio.run(c.flush())

        result = c.query()
        assert result["h24"]["download"] == 1
        assert result["d7"]["top_reports"] == [{"report_id": "00001-TST", "hits": 1}]

    def test_failed_response_does_not_count_as_hit(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        now = _now()
        c.record("/api/search", "1.1.1.1", 404, now)
        _gesture(c, "1.1.1.1", now)
        asyncio.run(c.flush())

        result = c.query()
        assert result["h24"]["search"] == 0
        assert result["h24"]["errors_4xx"] == 1
        assert result["h24"]["unique_ips"] == 1

    def test_hour_window_is_shared(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        now = _now()
        c.record("/api/search", "1.1.1.1", 200, now - timedelta(hours=2))
        _gesture(c, "1.1.1.1", now - timedelta(hours=2))
        c.record("/api/search", "2.2.2.2", 200, now - timedelta(hours=25))
        _gesture(c, "2.2.2.2", now - timedelta(hours=25))
        asyncio.run(c.flush())

        result = c.query()
        assert result["h24"]["unique_ips"] == 1
        assert result["h24"]["search"] == 1
        assert result["d7"]["unique_ips"] == 2
        assert result["d7"]["search"] == 2

    def test_gesture_outside_window_does_not_make_human(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        now = _now()
        _gesture(c, "1.2.3.4", now - timedelta(hours=30))
        c.record("/api/search", "1.2.3.4", 200, now - timedelta(hours=1))
        asyncio.run(c.flush())

        result = c.query()
        assert result["h24"]["unique_ips"] == 0
        assert result["h24"]["bot_ips"] == 1
        assert result["h24"]["search"] == 0
        assert result["d30"]["unique_ips"] == 1
        assert result["d30"]["search"] == 1
        assert result["d30"]["bot_ips"] == 0
