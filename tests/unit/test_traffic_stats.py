# Unit tests for middlewares/traffic_stats.py
# Описание: Проверяет функции категоризации запросов (_categorize, _report_id_from_path,
#           _report_id_from_page), признак бота (_is_bot) и StatsCollector (record, flush, query).
#           StatsCollector принимает db_path параметром — патчинг config не нужен.
#           flush() — async, запускается через asyncio.run().

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
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
        assert not any(c._hourly_ips.values())

    def test_unique_ips_counted_per_hour(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/api/search", "1.1.1.1", 200, _now())
        c.record("/api/search", "2.2.2.2", 200, _now())
        c.record("/api/search", "1.1.1.1", 200, _now())  # дубль
        hour = _now().strftime("%Y-%m-%dT%H")
        assert len(c._hourly_ips[hour]) == 2

    def test_bot_skips_hits_but_counts_4xx(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        hour = _now().strftime("%Y-%m-%dT%H")
        c.record("/api/search", "9.9.9.9", 200, _now(), is_bot=True)
        c.record("/?42-TST", "9.9.9.9", 200, _now(), is_bot=True)
        c.record("/data/00001-TST.zip", "9.9.9.9", 404, _now(), is_bot=True)
        assert not any(c._hourly_hits.values())
        assert not any(c._popular.values())
        assert not any(c._report_views.values())
        assert "9.9.9.9" in c._hourly_bot_ips[hour]
        assert "9.9.9.9" not in c._hourly_ips[hour]
        assert c._hourly_errors[hour]["4xx"] == 1

    def test_human_after_bot_takes_ip(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        hour = _now().strftime("%Y-%m-%dT%H")
        c.record("/api/search", "1.2.3.4", 200, _now(), is_bot=True)
        c.record("/api/search", "1.2.3.4", 200, _now(), is_bot=False)
        assert "1.2.3.4" in c._hourly_ips[hour]
        assert "1.2.3.4" not in c._hourly_bot_ips[hour]
        assert c._hourly_hits[hour]["search"] == 1


class TestStatsCollectorFlush:
    def test_flush_writes_to_db(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/api/search", "1.2.3.4", 200, _now())
        c.record("/data/00001-TST.zip", "1.2.3.4", 200, _now())

        asyncio.run(c.flush())

        conn = sqlite3.connect(str(tmp_path / "stats.db"))
        hourly = conn.execute("SELECT SUM(hits) FROM hourly_stats").fetchone()[0]
        popular = conn.execute("SELECT COUNT(*) FROM daily_popular").fetchone()[0]
        hourly_ips = conn.execute("SELECT COUNT(*) FROM hourly_ips").fetchone()[0]
        conn.close()

        assert hourly and hourly >= 2
        assert popular >= 1
        assert hourly_ips >= 1

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

    def test_later_bot_flush_keeps_human(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/api/search", "1.2.3.4", 200, _now(), is_bot=True)
        asyncio.run(c.flush())
        c.record("/api/search", "1.2.3.4", 200, _now(), is_bot=False)
        asyncio.run(c.flush())
        c.record("/api/search", "1.2.3.4", 200, _now(), is_bot=True)
        asyncio.run(c.flush())

        result = c.query()
        assert result["h24"]["unique_ips"] == 1
        assert result["h24"]["bot_ips"] == 0

    def test_legacy_daily_ips_move_to_hour(self, tmp_path):
        db_path = tmp_path / "stats.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE daily_ips (date TEXT NOT NULL, ip TEXT NOT NULL, PRIMARY KEY (date, ip))"
        )
        today = _now().strftime("%Y-%m-%d")
        conn.execute("INSERT INTO daily_ips (date, ip) VALUES (?, '1.1.1.1')", (today,))
        conn.commit()
        conn.close()

        c = _collector(db_path)
        c.record("/api/search", "2.2.2.2", 200, _now(), is_bot=True)
        asyncio.run(c.flush())

        conn = sqlite3.connect(str(db_path))
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        old = conn.execute(
            "SELECT hour, bot FROM hourly_ips WHERE ip = '1.1.1.1'"
        ).fetchone()
        new = conn.execute(
            "SELECT bot FROM hourly_ips WHERE ip = '2.2.2.2'"
        ).fetchone()[0]
        conn.close()
        assert "daily_ips" not in tables
        assert old == (today + "T00", 0)
        assert new == 1


class TestIsBot:
    def _bot(self, ua: str, accept_language: str = "ru") -> bool:
        from middlewares.traffic_stats import _is_bot
        return _is_bot(ua, accept_language)

    def test_googlebot(self):
        assert self._bot("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)")

    def test_empty_ua(self):
        assert self._bot("")

    @pytest.mark.parametrize("ua", [
        "Mozilla/5.0 (compatible; YandexImages/3.0; +http://yandex.com/bots)",
        "Mozilla/5.0 (compatible; Linux x86_64; Mail.RU_Bot/2.0; +https://help.mail.ru/webmaster/indexing/robots)",
        "python-httpx/0.27.0",
        "Python-urllib/3.12",
    ])
    def test_other_crawlers(self, ua):
        assert self._bot(ua)

    @pytest.mark.parametrize("ua", [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 YaBrowser/24.1.0.0 Safari/537.36",
    ])
    def test_browsers_are_human(self, ua):
        assert not self._bot(ua)

    def test_browser_without_accept_language_is_bot(self):
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        assert self._bot(ua, "")
        assert self._bot(ua, "   ")
        assert not self._bot(ua, "ru-RU,ru;q=0.9")


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

    def test_query_splits_humans_and_bots(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        c.record("/api/search", "1.1.1.1", 200, _now())
        c.record("/?42-TST", "8.8.8.8", 200, _now(), is_bot=True)
        result = c.query()
        assert result["h24"]["unique_ips"] == 1
        assert result["h24"]["bot_ips"] == 1
        assert result["h24"]["search"] == 1
        assert result["h24"]["report"] == 0
        assert result["d7"]["bot_ips"] == 1
        assert result["d30"]["unique_ips"] == 1

    def test_unique_ips_use_same_hour_window_as_hits(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        now = _now()
        c.record("/api/search", "1.1.1.1", 200, now - timedelta(hours=2))
        c.record("/api/search", "2.2.2.2", 200, now - timedelta(hours=25))
        asyncio.run(c.flush())
        result = c.query()
        assert result["h24"]["unique_ips"] == 1
        assert result["h24"]["search"] == 1
        assert result["d7"]["unique_ips"] == 2
        assert result["d7"]["search"] == 2

    def test_human_outside_window_does_not_clear_bot(self, tmp_path):
        c = _collector(tmp_path / "stats.db")
        now = _now()
        c.record("/api/search", "1.2.3.4", 200, now - timedelta(hours=30))
        c.record("/api/search", "1.2.3.4", 200, now - timedelta(hours=1), is_bot=True)
        asyncio.run(c.flush())
        result = c.query()
        assert result["h24"]["unique_ips"] == 0
        assert result["h24"]["bot_ips"] == 1
        assert result["h24"]["search"] == 0
        assert result["d7"]["unique_ips"] == 1
        assert result["d7"]["bot_ips"] == 0
