# Traffic Stats Middleware для TlibWebApp
# Описание: Сбор статистики посещений. Человек — IP, приславший маячок жеста
#           (POST /api/gesture) в том же часовом окне, что и хиты: 24ч / 7д / 30д.
#           Остальные IP — боты, показывается только их число. Хиты и топы считаются
#           при выдаче и только по людям, поэтому просмотр до жеста не теряется.
#           Хит — ответ 200 или 304. Ошибки 4xx/5xx и «В кэш» — показатели сервера,
#           считаются по всем запросам страниц и API. Выдача читает только stats.db:
#           страница и дайджест берут одни цифры, отставание не больше интервала сброса.
#           Таблица hits без rowid: ключ не хранится второй раз. Старые таблицы
#           посещений удаляются при старте — в них нет жеста.

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
    STATS_GESTURE_PATH,
    STATS_RETENTION_DAYS,
)
from logging_config import app_logger


# ---------------------------------------------------------------------------
# Константы категоризации
# ---------------------------------------------------------------------------

_SEARCH_PATH = "/api/search"
_PAGE_PATHS = frozenset(["/", "/index.html", "/about.html", "/admin"])
_HIT_CATEGORIES = ("search", "report", "download", "api")
# Ошибки пишем для страниц и API. Статика и сканерные пути только отмечают IP.
_ERROR_CATEGORIES = frozenset(("search", "report", "download", "page", "api", "gesture"))
_HIT_STATUSES = frozenset((200, 304))
_REPORT_RE = re.compile(r"^(\d+(?:-[а-яА-Яa-zA-Z0-9]{1,10})?)\..*$")
# Шифр отчёта в query string: /?12345 или /?12345-ABC
_REPORT_PAGE_RE = re.compile(r"^\?\d+(?:-[а-яА-Яa-zA-Z0-9]{1,10})?$")
_PERIODS = (
    ("h24", timedelta(hours=24)),
    ("d7", timedelta(days=7)),
    ("d30", timedelta(days=30)),
)


def _categorize(path: str) -> str:
    """
    Категория запроса. gesture — маячок человека. other — статика, сканеры и всё,
    что не является хитом: такие запросы нужны, чтобы IP попал в число ботов.
    /api/search с query (?page=1, ?auto=1) — api, а не поиск: это догрузка и автопоиск.
    """
    if path == STATS_GESTURE_PATH or path.startswith(STATS_GESTURE_PATH + "?"):
        return "gesture"
    if path.startswith(LOCAL_ARCHIVE_PATH + "/"):
        return "download"
    if path == _SEARCH_PATH:
        return "search"
    if path.startswith("/?") and _REPORT_PAGE_RE.match(path[1:]):
        return "report"
    if path in _PAGE_PATHS:
        return "page"
    if path.startswith("/api/"):
        return "api"
    return "other"


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


def _report_id_for(category: str, path: str) -> str:
    if category == "download":
        return _report_id_from_path(path) or ""
    if category == "report":
        return _report_id_from_page(path) or ""
    return ""


# ---------------------------------------------------------------------------
# StatsCollector
# ---------------------------------------------------------------------------

class StatsCollector:
    """
    In-memory счётчики посещений с периодическим сбросом в stats.db.

    Все операции записи (record) происходят в одном asyncio event loop
    и не требуют блокировок. asyncio.Lock защищает flush от конкурентных
    вызовов (например, если flush и ручной сброс совпадут по времени).
    query() память не читает: его вызывает поток пула, а record — цикл событий.
    """

    def __init__(self, db_path: str, retention_days: int) -> None:
        self._db_path = db_path
        self._retention_days = retention_days
        self._lock = asyncio.Lock()

        # (час, ip, категория, шифр) -> число запросов
        self._hits: Counter = Counter()
        # hourly_errors[hour]["4xx"|"5xx"] -> количество ошибок
        self._hourly_errors: dict[str, Counter] = defaultdict(Counter)
        # hourly_cached[hour] -> количество отчётов, добавленных в кэш
        self._hourly_cached: dict[str, int] = defaultdict(int)

        self._init_db()

    # ---- Инициализация БД --------------------------------------------------

    def _init_db(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS hits (
                hour      TEXT NOT NULL,
                ip        TEXT NOT NULL,
                category  TEXT NOT NULL,
                report_id TEXT NOT NULL,
                hits      INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (hour, ip, category, report_id)
            ) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS hourly_stats (
                hour       TEXT NOT NULL,
                category   TEXT NOT NULL,
                hits       INTEGER DEFAULT 0,
                errors_4xx INTEGER DEFAULT 0,
                errors_5xx INTEGER DEFAULT 0,
                PRIMARY KEY (hour, category)
            );
            DROP TABLE IF EXISTS hourly_ips;
            DROP TABLE IF EXISTS daily_popular;
            DROP TABLE IF EXISTS daily_report_views;
            DROP TABLE IF EXISTS daily_ips;
            DELETE FROM hourly_stats WHERE category NOT IN ('_errors', '_cached');
        """)
        conn.commit()
        conn.close()

    # ---- Запись события (вызывается из middleware) --------------------------

    def record(self, path: str, ip: str, status: int, dt: datetime) -> None:
        """
        Фиксирует один запрос. Вызывается синхронно в asyncio event loop
        после получения ответа — блокировка не требуется.

        Ответ не 200 и не 304 отмечает IP, но хитом не считается.
        """
        category = _categorize(path)
        hour = _hour_key(dt)

        if category in _ERROR_CATEGORIES:
            if status >= 500:
                self._hourly_errors[hour]["5xx"] += 1
            elif status >= 400:
                self._hourly_errors[hour]["4xx"] += 1

        if status not in _HIT_STATUSES:
            category = "other"
            report_id = ""
        else:
            report_id = _report_id_for(category, path)

        self._hits[(hour, ip, category, report_id)] += 1

    def record_cache_prepared(self) -> None:
        """Фиксирует успешное добавление одного отчёта в кэш."""
        hour = _hour_key(datetime.now(timezone.utc))
        self._hourly_cached[hour] += 1

    # ---- Сброс данных в stats.db (фоновая задача) --------------------------

    async def flush(self) -> None:
        """Сбрасывает in-memory данные в stats.db под asyncio.Lock.

        Атомарно подменяет счётчики на пустые внутри event loop, затем передаёт
        снимки в executor. Новые вызовы record() после swap пишут уже в свежие
        счётчики и не попадут в этот flush.
        """
        async with self._lock:
            snapshot_hits = self._hits
            snapshot_errors = self._hourly_errors
            snapshot_cached = self._hourly_cached

            self._hits = Counter()
            self._hourly_errors = defaultdict(Counter)
            self._hourly_cached = defaultdict(int)

            await asyncio.get_event_loop().run_in_executor(
                None, self._flush_sync, snapshot_hits, snapshot_errors, snapshot_cached,
            )

    def _flush_sync(self, hits: Counter, errors: dict, cached: dict) -> None:
        """Записывает переданные снимки in-memory данных в stats.db.

        Принимает снимки, сделанные в flush() после атомарного swap, поэтому
        каждый хит попадает в БД ровно один раз -- дублирование исключено.
        """
        try:
            conn = sqlite3.connect(self._db_path, timeout=10)
            now = datetime.now(timezone.utc)
            cutoff_hour = (now - timedelta(days=self._retention_days)).strftime("%Y-%m-%d") + "T00"

            if hits:
                conn.executemany(
                    """
                    INSERT INTO hits (hour, ip, category, report_id, hits)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(hour, ip, category, report_id) DO UPDATE SET
                        hits = hits + excluded.hits
                    """,
                    (
                        (hour, ip, category, report_id, count)
                        for (hour, ip, category, report_id), count in hits.items()
                    ),
                )

            for hour, errs in errors.items():
                e4 = errs.get("4xx", 0)
                e5 = errs.get("5xx", 0)
                if e4 or e5:
                    conn.execute(
                        """
                        INSERT INTO hourly_stats (hour, category, hits, errors_4xx, errors_5xx)
                        VALUES (?, '_errors', 0, ?, ?)
                        ON CONFLICT(hour, category) DO UPDATE SET
                            errors_4xx = errors_4xx + excluded.errors_4xx,
                            errors_5xx = errors_5xx + excluded.errors_5xx
                        """,
                        (hour, e4, e5),
                    )

            for hour, count in cached.items():
                if count:
                    conn.execute(
                        """
                        INSERT INTO hourly_stats (hour, category, hits, errors_4xx, errors_5xx)
                        VALUES (?, '_cached', ?, 0, 0)
                        ON CONFLICT(hour, category) DO UPDATE SET
                            hits = hits + excluded.hits
                        """,
                        (hour, count),
                    )

            conn.execute("DELETE FROM hits WHERE hour < ?", (cutoff_hour,))
            conn.execute("DELETE FROM hourly_stats WHERE hour < ?", (cutoff_hour,))

            conn.commit()
            conn.close()

        except Exception as e:
            app_logger.error(f"[traffic_stats] Ошибка flush: {e}")

    # ---- Запрос данных для панели администратора ---------------------------

    def query(self) -> dict:
        """
        Статистика за 24ч / 7д / 30д из stats.db.
        unique_ips — люди (маячок в окне), bot_ips — остальные IP.
        Хиты и топы только по людям. Окно одно: час >= границы.
        Может отставать на интервал сброса: память сюда не подмешивается.
        """
        try:
            return self._query_sync()
        except Exception as e:
            app_logger.error(f"[traffic_stats] Ошибка query: {e}")
            return {}

    def _query_sync(self) -> dict:
        now = datetime.now(timezone.utc)
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row

        since_row = conn.execute("SELECT MIN(hour) FROM hits").fetchone()
        since = since_row[0] if since_row else None

        result = {"since": since}
        for name, delta in _PERIODS:
            result[name] = self._period(conn, _hour_key(now - delta))
        conn.close()
        return result

    def _period(self, conn: sqlite3.Connection, h_cut: str) -> dict:
        humans = conn.execute(
            "SELECT COUNT(DISTINCT ip) FROM hits WHERE hour >= ? AND category = 'gesture'",
            (h_cut,),
        ).fetchone()[0] or 0
        total = conn.execute(
            "SELECT COUNT(DISTINCT ip) FROM hits WHERE hour >= ?",
            (h_cut,),
        ).fetchone()[0] or 0

        hits = {category: 0 for category in _HIT_CATEGORIES}
        for row in conn.execute(
            """
            SELECT category, SUM(hits) AS total FROM hits
            WHERE hour >= ?
              AND category IN ('search', 'report', 'download', 'api')
              AND ip IN (
                  SELECT ip FROM hits WHERE hour >= ? AND category = 'gesture'
              )
            GROUP BY category
            """,
            (h_cut, h_cut),
        ):
            hits[row["category"]] = row["total"] or 0

        top_views = [
            {"report_id": row["report_id"], "unique_ips": row["unique_ips"]}
            for row in conn.execute(
                """
                SELECT report_id, COUNT(DISTINCT ip) AS unique_ips FROM hits
                WHERE hour >= ? AND category = 'report' AND report_id != ''
                  AND ip IN (
                      SELECT ip FROM hits WHERE hour >= ? AND category = 'gesture'
                  )
                GROUP BY report_id
                ORDER BY unique_ips DESC, report_id
                LIMIT 10
                """,
                (h_cut, h_cut),
            )
        ]
        top_reports = [
            {"report_id": row["report_id"], "hits": row["total"]}
            for row in conn.execute(
                """
                SELECT report_id, SUM(hits) AS total FROM hits
                WHERE hour >= ? AND category = 'download' AND report_id != ''
                  AND ip IN (
                      SELECT ip FROM hits WHERE hour >= ? AND category = 'gesture'
                  )
                GROUP BY report_id
                ORDER BY total DESC, report_id
                LIMIT 10
                """,
                (h_cut, h_cut),
            )
        ]

        err4 = 0
        err5 = 0
        cached = 0
        for row in conn.execute(
            "SELECT category, SUM(hits) AS h, SUM(errors_4xx) AS e4, SUM(errors_5xx) AS e5 "
            "FROM hourly_stats WHERE hour >= ? GROUP BY category",
            (h_cut,),
        ):
            if row["category"] == "_errors":
                err4 += row["e4"] or 0
                err5 += row["e5"] or 0
            elif row["category"] == "_cached":
                cached += row["h"] or 0

        return {
            **hits,
            "unique_ips": humans,
            "bot_ips": total - humans,
            "cached_reports": cached,
            "errors_4xx": err4,
            "errors_5xx": err5,
            "top_report_views": top_views,
            "top_reports": top_reports,
        }


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class TrafficStatsMiddleware(BaseHTTPMiddleware):
    """
    Middleware для сбора статистики посещений.
    Записывает каждый запрос в StatsCollector после получения ответа.
    HEAD пропускается: краулеры не должны раздувать просмотры и уникальные IP.
    StatsCollector должен быть инициализирован и сохранён в app.state.stats_collector.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        try:
            collector: StatsCollector | None = getattr(
                request.app.state, "stats_collector", None
            )
            if collector is not None and request.method != "HEAD":
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
