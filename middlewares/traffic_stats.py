# Version 1.2 - 30.03.2026
# Traffic Stats Middleware для TlibWebApp
# Описание: Сбор статистики посещений сайта. Классифицирует запросы по категориям
#           (search, report, download, api, page), считает уникальные IP по дням,
#           популярные отчёты по скачиваниям и по просмотрам (уникальные IP).
#           Хранит in-memory счётчики, сбрасывает в stats.db каждые
#           STATS_FLUSH_INTERVAL секунд. Данные старше STATS_RETENTION_DAYS
#           удаляются автоматически. Статические ресурсы (JS/CSS/assets) не учитываются.

import asyncio
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from config import (
    LOCAL_ARCHIVE_PATH,
    STATIC_DIRS,
    FAVICON_URL_PATH,
    STATS_DB_PATH,
    STATS_RETENTION_DAYS,
)
from logging_config import app_logger


# ---------------------------------------------------------------------------
# Константы категоризации
# ---------------------------------------------------------------------------

_SEARCH_PREFIX = "/api/search"
_PAGE_PATHS = frozenset(["/", "/index.html", "/about.html", "/admin"])
# Префиксы статических ресурсов, которые не учитываются в статистике
_STATIC_SKIP_PREFIXES = tuple(f"/{d}/" for d in STATIC_DIRS)
_REPORT_RE = re.compile(r"^(\d+(?:-[а-яА-Яa-zA-Z0-9]{1,10})?)\..*$")
# Шифр отчёта в query string: /?12345 или /?12345-ABC
_REPORT_PAGE_RE = re.compile(r"^\?\d+(?:-[а-яА-Яa-zA-Z0-9]{1,10})?$")


def _categorize(path: str) -> str | None:
    """
    Возвращает категорию запроса: 'search', 'report', 'download', 'page', 'api',
    или None для статических ресурсов (не учитываются в статистике).
    """
    if path.startswith(_STATIC_SKIP_PREFIXES) or path == FAVICON_URL_PATH:
        return None
    if path.startswith(LOCAL_ARCHIVE_PATH + "/"):
        return "download"
    if path == _SEARCH_PREFIX:
        return "search"
    if path.startswith("/?") and _REPORT_PAGE_RE.match(path[1:]):
        return "report"
    if path in _PAGE_PATHS:
        return "page"
    if path.startswith("/api/"):
        return "api"
    return None


def _report_id_from_path(path: str) -> str | None:
    """
    Извлекает идентификатор отчёта из пути вида /data/12345-TSSR.zip.
    Возвращает '12345-TSSR' или '12345', либо None если не распознан.
    """
    filename = path.rsplit("/", 1)[-1]
    m = _REPORT_RE.match(filename)
    return m.group(1) if m else None


def _report_id_from_page(path: str) -> str | None:
    """
    Извлекает шифр отчёта из пути страницы вида /?12345-ABC.
    Возвращает '12345-ABC' или '12345', либо None если формат не совпадает.
    """
    if not path.startswith("/?"):
        return None
    q = path[2:]
    return q if _REPORT_PAGE_RE.match("?" + q) else None


def _hour_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H")


def _date_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# StatsCollector
# ---------------------------------------------------------------------------

class StatsCollector:
    """
    In-memory счётчики посещений с периодическим сбросом в stats.db.

    Все операции записи (record) происходят в одном asyncio event loop
    и не требуют блокировок. asyncio.Lock защищает flush от конкурентных
    вызовов (например, если flush и ручной сброс совпадут по времени).
    """

    def __init__(self, db_path: str, retention_days: int) -> None:
        self._db_path = db_path
        self._retention_days = retention_days
        self._lock = asyncio.Lock()

        # hourly_hits[hour][category] -> количество запросов
        self._hourly_hits: dict[str, Counter] = defaultdict(Counter)
        # hourly_errors[hour]["4xx"|"5xx"] -> количество ошибок
        self._hourly_errors: dict[str, Counter] = defaultdict(Counter)
        # hourly_cached[hour] -> количество отчётов, добавленных в кэш
        self._hourly_cached: dict[str, int] = defaultdict(int)
        # daily_ips[date] -> множество уникальных IP
        self._daily_ips: dict[str, set] = defaultdict(set)
        # popular[date][report_id] -> количество скачиваний
        self._popular: dict[str, Counter] = defaultdict(Counter)
        # report_views[date][report_id] -> множество IP, просмотревших страницу отчёта
        self._report_views: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))

        self._init_db()

    # ---- Инициализация БД --------------------------------------------------

    def _init_db(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS hourly_stats (
                hour       TEXT NOT NULL,
                category   TEXT NOT NULL,
                hits       INTEGER DEFAULT 0,
                errors_4xx INTEGER DEFAULT 0,
                errors_5xx INTEGER DEFAULT 0,
                PRIMARY KEY (hour, category)
            );
            CREATE TABLE IF NOT EXISTS daily_ips (
                date TEXT NOT NULL,
                ip   TEXT NOT NULL,
                PRIMARY KEY (date, ip)
            );
            CREATE TABLE IF NOT EXISTS daily_popular (
                date      TEXT NOT NULL,
                report_id TEXT NOT NULL,
                hits      INTEGER DEFAULT 0,
                PRIMARY KEY (date, report_id)
            );
            CREATE TABLE IF NOT EXISTS daily_report_views (
                date      TEXT NOT NULL,
                report_id TEXT NOT NULL,
                ip        TEXT NOT NULL,
                PRIMARY KEY (date, report_id, ip)
            );
        """)
        conn.commit()
        conn.close()

    # ---- Запись события (вызывается из middleware) --------------------------

    def record(self, path: str, ip: str, status: int, dt: datetime) -> None:
        """
        Фиксирует один запрос. Вызывается синхронно в asyncio event loop
        после получения ответа — блокировка не требуется.
        """
        category = _categorize(path)
        if category is None:
            return

        hour = _hour_key(dt)
        date = _date_key(dt)

        self._hourly_hits[hour][category] += 1

        if status >= 500:
            self._hourly_errors[hour]["5xx"] += 1
        elif status >= 400:
            self._hourly_errors[hour]["4xx"] += 1

        self._daily_ips[date].add(ip)

        if category == "download":
            rid = _report_id_from_path(path)
            if rid:
                self._popular[date][rid] += 1
        elif category == "report":
            rid = _report_id_from_page(path)
            if rid:
                self._report_views[date][rid].add(ip)

    def record_cache_prepared(self) -> None:
        """Фиксирует успешное добавление одного отчёта в кэш."""
        hour = _hour_key(datetime.now(timezone.utc))
        self._hourly_cached[hour] += 1

    # ---- Сброс данных в stats.db (фоновая задача) --------------------------

    async def flush(self) -> None:
        """Сбрасывает in-memory данные в stats.db под asyncio.Lock.

        Атомарно подменяет словари на пустые внутри event loop (GIL гарантирует
        атомарность присваивания), затем передаёт снимки в executor. Новые вызовы
        record() после swap пишут уже в свежие словари и не попадут в этот flush.
        """
        async with self._lock:
            snapshot_hits         = self._hourly_hits
            snapshot_errors       = self._hourly_errors
            snapshot_cached       = self._hourly_cached
            snapshot_ips          = self._daily_ips
            snapshot_popular      = self._popular
            snapshot_report_views = self._report_views

            self._hourly_hits   = defaultdict(Counter)
            self._hourly_errors = defaultdict(Counter)
            self._hourly_cached = defaultdict(int)
            self._daily_ips     = defaultdict(set)
            self._popular       = defaultdict(Counter)
            self._report_views  = defaultdict(lambda: defaultdict(set))

            await asyncio.get_event_loop().run_in_executor(
                None, self._flush_sync,
                snapshot_hits, snapshot_errors, snapshot_cached,
                snapshot_ips, snapshot_popular, snapshot_report_views,
            )

    def _flush_sync(
        self,
        hits: dict,
        errors: dict,
        cached: dict,
        ips: dict,
        popular: dict,
        report_views: dict,
    ) -> None:
        """Записывает переданные снимки in-memory данных в stats.db.

        Принимает снимки, сделанные в flush() после атомарного swap, поэтому
        каждый хит попадает в БД ровно один раз -- дублирование исключено.
        """
        try:
            conn = sqlite3.connect(self._db_path, timeout=10)
            now = datetime.now(timezone.utc)
            cutoff_date = (now - timedelta(days=self._retention_days)).strftime("%Y-%m-%d")
            cutoff_hour = cutoff_date + "T00"

            # hourly_stats
            all_hours = set(hits) | set(errors) | set(cached)
            for hour in all_hours:
                cats = hits.get(hour, Counter())
                errs = errors.get(hour, Counter())
                e4 = errs.get("4xx", 0)
                e5 = errs.get("5xx", 0)
                for cat, cnt in cats.items():
                    conn.execute("""
                        INSERT INTO hourly_stats (hour, category, hits, errors_4xx, errors_5xx)
                        VALUES (?, ?, ?, 0, 0)
                        ON CONFLICT(hour, category) DO UPDATE SET
                            hits = hits + excluded.hits
                    """, (hour, cat, cnt))
                if e4 or e5:
                    conn.execute("""
                        INSERT INTO hourly_stats (hour, category, hits, errors_4xx, errors_5xx)
                        VALUES (?, '_errors', 0, ?, ?)
                        ON CONFLICT(hour, category) DO UPDATE SET
                            errors_4xx = errors_4xx + excluded.errors_4xx,
                            errors_5xx = errors_5xx + excluded.errors_5xx
                    """, (hour, e4, e5))
                cnt_cached = cached.get(hour, 0)
                if cnt_cached:
                    conn.execute("""
                        INSERT INTO hourly_stats (hour, category, hits, errors_4xx, errors_5xx)
                        VALUES (?, '_cached', ?, 0, 0)
                        ON CONFLICT(hour, category) DO UPDATE SET
                            hits = hits + excluded.hits
                    """, (hour, cnt_cached))

            # daily_ips
            for date, ip_set in ips.items():
                for ip in ip_set:
                    conn.execute(
                        "INSERT OR IGNORE INTO daily_ips (date, ip) VALUES (?, ?)",
                        (date, ip)
                    )

            # daily_popular
            for date, rids in popular.items():
                for rid, cnt in rids.items():
                    conn.execute("""
                        INSERT INTO daily_popular (date, report_id, hits)
                        VALUES (?, ?, ?)
                        ON CONFLICT(date, report_id) DO UPDATE SET
                            hits = hits + excluded.hits
                    """, (date, rid, cnt))

            # daily_report_views (уникальные IP на страницах отчётов)
            for date, rids in report_views.items():
                for rid, ip_set in rids.items():
                    for ip in ip_set:
                        conn.execute(
                            "INSERT OR IGNORE INTO daily_report_views (date, report_id, ip) VALUES (?, ?, ?)",
                            (date, rid, ip),
                        )

            # Удаляем данные старше STATS_RETENTION_DAYS
            conn.execute("DELETE FROM hourly_stats WHERE hour < ?", (cutoff_hour,))
            conn.execute("DELETE FROM daily_ips WHERE date < ?", (cutoff_date,))
            conn.execute("DELETE FROM daily_popular WHERE date < ?", (cutoff_date,))
            conn.execute("DELETE FROM daily_report_views WHERE date < ?", (cutoff_date,))

            conn.commit()
            conn.close()

        except Exception as e:
            app_logger.error(f"[traffic_stats] Ошибка flush: {e}")

    # ---- Запрос данных для панели администратора ---------------------------

    def query(self) -> dict:
        """
        Возвращает агрегированную статистику за 24ч / 7д / 30д.
        Читает из stats.db (данные могут отставать на STATS_FLUSH_INTERVAL)
        и дополняет текущими in-memory счётчиками, накопленными с последнего flush.
        """
        try:
            return self._query_sync()
        except Exception as e:
            app_logger.error(f"[traffic_stats] Ошибка query: {e}")
            return {}

    def _query_sync(self) -> dict:
        now = datetime.now(timezone.utc)

        # Границы периодов (час и дата)
        cutoff_hour = {
            "h24": _hour_key(now - timedelta(hours=24)),
            "d7":  _hour_key(now - timedelta(days=7)),
            "d30": _hour_key(now - timedelta(days=30)),
        }
        cutoff_date = {
            "h24": _date_key(now - timedelta(days=1)),
            "d7":  _date_key(now - timedelta(days=7)),
            "d30": _date_key(now - timedelta(days=30)),
        }

        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row

        # --- Хиты по категориям из DB ---
        db_hits: dict[str, dict[str, int]] = {p: {} for p in ("h24", "d7", "d30")}
        db_err4: dict[str, int] = {}
        db_err5: dict[str, int] = {}
        db_cached: dict[str, int] = {}

        for period, h_cut in cutoff_hour.items():
            rows = conn.execute(
                "SELECT category, SUM(hits) AS h, SUM(errors_4xx) AS e4, SUM(errors_5xx) AS e5 "
                "FROM hourly_stats WHERE hour >= ? GROUP BY category",
                (h_cut,)
            ).fetchall()
            for row in rows:
                if row["category"] == "_errors":
                    db_err4[period] = (db_err4.get(period, 0) + (row["e4"] or 0))
                    db_err5[period] = (db_err5.get(period, 0) + (row["e5"] or 0))
                elif row["category"] == "_cached":
                    db_cached[period] = (db_cached.get(period, 0) + (row["h"] or 0))
                else:
                    db_hits[period][row["category"]] = row["h"] or 0

        # --- Уникальные IP из DB ---
        db_ips: dict[str, int] = {}
        for period, d_cut in cutoff_date.items():
            row = conn.execute(
                "SELECT COUNT(DISTINCT ip) FROM daily_ips WHERE date >= ?",
                (d_cut,)
            ).fetchone()
            db_ips[period] = row[0] if row else 0

        # --- Топ скачиваний за 7 дней из DB ---
        top_rows = conn.execute(
            "SELECT report_id, SUM(hits) AS total FROM daily_popular "
            "WHERE date >= ? GROUP BY report_id ORDER BY total DESC LIMIT 10",
            (cutoff_date["d7"],)
        ).fetchall()
        top_reports = [{"report_id": r["report_id"], "hits": r["total"]} for r in top_rows]

        # --- Топ отчётов по просмотрам (уникальные IP) за 7 дней из DB ---
        top_view_rows = conn.execute(
            "SELECT report_id, COUNT(DISTINCT ip) AS unique_ips FROM daily_report_views "
            "WHERE date >= ? GROUP BY report_id ORDER BY unique_ips DESC LIMIT 10",
            (cutoff_date["d7"],)
        ).fetchall()
        top_report_views = [{"report_id": r["report_id"], "unique_ips": r["unique_ips"]} for r in top_view_rows]

        conn.close()

        # Добавляем in-memory popular (данные с момента последнего flush, ещё не в БД)
        top_map = {r["report_id"]: r["hits"] for r in top_reports}
        for date, rids in self._popular.items():
            if date >= cutoff_date["d7"]:
                for rid, count in rids.items():
                    top_map[rid] = top_map.get(rid, 0) + count
        top_reports = sorted(
            [{"report_id": k, "hits": v} for k, v in top_map.items()],
            key=lambda x: x["hits"], reverse=True,
        )[:10]

        # Добавляем in-memory report_views (уникальные IP по просмотрам отчётов)
        view_map: dict[str, set] = {r["report_id"]: set() for r in top_report_views}
        for date, rids in self._report_views.items():
            if date >= cutoff_date["d7"]:
                for rid, ip_set in rids.items():
                    if rid not in view_map:
                        view_map[rid] = set()
                    view_map[rid].update(ip_set)
        # DB хранит точные уникальные IP; in-memory может добавить новые.
        # Для отчётов, уже известных из DB, берём max(db_count, len(mem_ips)).
        db_view_counts = {r["report_id"]: r["unique_ips"] for r in top_report_views}
        merged_view: dict[str, int] = {}
        for rid, ips in view_map.items():
            merged_view[rid] = max(db_view_counts.get(rid, 0), len(ips))
        for rid, cnt in db_view_counts.items():
            if rid not in merged_view:
                merged_view[rid] = cnt
        top_report_views = sorted(
            [{"report_id": k, "unique_ips": v} for k, v in merged_view.items()],
            key=lambda x: x["unique_ips"], reverse=True,
        )[:10]

        # --- Добавляем текущие in-memory данные (с момента последнего flush) ---
        for hour, cats in self._hourly_hits.items():
            for period, h_cut in cutoff_hour.items():
                if hour >= h_cut:
                    for cat, count in cats.items():
                        db_hits[period][cat] = db_hits[period].get(cat, 0) + count

        for hour, errs in self._hourly_errors.items():
            for period, h_cut in cutoff_hour.items():
                if hour >= h_cut:
                    db_err4[period] = db_err4.get(period, 0) + errs.get("4xx", 0)
                    db_err5[period] = db_err5.get(period, 0) + errs.get("5xx", 0)

        for hour, cnt in self._hourly_cached.items():
            for period, h_cut in cutoff_hour.items():
                if hour >= h_cut:
                    db_cached[period] = db_cached.get(period, 0) + cnt

        # IP из memory: берём максимум (DB уже содержит ранее записанные уникальные IP)
        for period, d_cut in cutoff_date.items():
            mem_ips: set = set()
            for d, ips in self._daily_ips.items():
                if d >= d_cut:
                    mem_ips.update(ips)
            db_ips[period] = max(db_ips[period], len(mem_ips))

        def _cats(period: str) -> dict:
            h = db_hits.get(period, {})
            return {
                "search":   h.get("search",   0),
                "report":   h.get("report",   0),
                "download": h.get("download", 0),
                "api":      h.get("api",      0),
            }

        return {
            "h24": {
                **_cats("h24"),
                "unique_ips":     db_ips.get("h24", 0),
                "cached_reports": db_cached.get("h24", 0),
                "errors_4xx":     db_err4.get("h24", 0),
                "errors_5xx":     db_err5.get("h24", 0),
            },
            "d7": {
                **_cats("d7"),
                "unique_ips":     db_ips.get("d7", 0),
                "cached_reports": db_cached.get("d7", 0),
            },
            "d30": {
                **_cats("d30"),
                "unique_ips":     db_ips.get("d30", 0),
                "cached_reports": db_cached.get("d30", 0),
            },
            "top_report_views": top_report_views,
            "top_reports": top_reports,
        }


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class TrafficStatsMiddleware(BaseHTTPMiddleware):
    """
    Middleware для сбора статистики посещений.
    Записывает каждый запрос в StatsCollector после получения ответа.
    StatsCollector должен быть инициализирован и сохранён в app.state.stats_collector.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        try:
            collector: StatsCollector | None = getattr(
                request.app.state, "stats_collector", None
            )
            if collector is not None:
                ip = request.client.host if request.client else "unknown"
                collector.record(
                    path=request.url.path + ('?' + request.url.query if request.url.query else ''),
                    ip=ip,
                    status=response.status_code,
                    dt=datetime.now(timezone.utc),
                )
        except Exception as e:
            app_logger.debug(f"[traffic_stats] Ошибка записи события: {e}")

        return response
