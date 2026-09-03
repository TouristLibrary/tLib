# Version 1.1 - 15.06.2026 17:18:00 GMT
# Сервис сбора операционного статуса для панели администратора.
# Описание: Функции сбора данных о здоровье системы, дисках, динамике пополнения,
#           трафике и событиях безопасности. Вызываются из admin_router.
#           Принимают app_state (Starlette State) вместо Request — сервис свободен от FastAPI.
# 1.1: UPLOAD_PAUSE_DIRECTORY перенесён в верхний блок from config import (единообразие, monkeypatch).

import os
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import (
    BACKUP_DIRECTORY,
    CACHE_DIRECTORY,
    DATABASE_PATH,
    DATABASE_TABLE_NAME,
    DATA_DIRECTORY,
    LOG_DIRECTORY,
    LOG_FILE_CRITICAL,
    MAX_CACHE_SIZE,
    STATE_DB_WATCHER_TASK,
    STATE_FILE_WATCHER_TASK,
    STATE_STARTED_AT,
    UPLOAD_DIRECTORY,
    UPLOAD_DONE_DIRECTORY,
    UPLOAD_ERROR_DIRECTORY,
    UPLOAD_GO_DIRECTORY,
    UPLOAD_PAUSE_DIRECTORY,
    UPLOAD_PROCESSING_DIRECTORY,
)
from services.database import open_tlib_db
from logging_config import app_logger, parse_logfmt_fields


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _dir_size_bytes(path: Path) -> int:
    """Рекурсивно считает суммарный размер файлов в директории."""
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    except Exception:
        pass
    return total


def _find_mountpoint(path: Path) -> str:
    """Возвращает точку монтирования для пути (поднимается по родителям)."""
    p = path.resolve()
    while not os.path.ismount(str(p)):
        parent = p.parent
        if parent == p:
            break
        p = parent
    return str(p)


# Регулярка для извлечения timestamp из строк critical.log
_LOG_TIMESTAMP_RE = re.compile(r'^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')


# ---------------------------------------------------------------------------
# Сбор данных
# ---------------------------------------------------------------------------

def collect_health(app_state) -> dict:
    """Раздел 1: здоровье системы — только индикаторы."""
    result: dict = {}

    # БД доступна
    db_path = Path(DATABASE_PATH)
    db_ok = False
    if db_path.exists():
        try:
            conn = open_tlib_db(row_factory=False)
            conn.execute("SELECT 1")
            conn.close()
            db_ok = True
        except Exception:
            pass
    result["db_accessible"] = db_ok

    # Директория data доступна
    data_path = Path(DATA_DIRECTORY)
    result["data_dir_accessible"] = data_path.exists() and data_path.is_dir()

    # Фоновые задачи
    def _task_running(attr: str) -> bool:
        try:
            task = getattr(app_state, attr, None)
            return task is not None and not task.done()
        except Exception:
            return False

    result["db_watcher_running"] = _task_running(STATE_DB_WATCHER_TASK)
    result["file_watcher_running"] = _task_running(STATE_FILE_WATCHER_TASK)

    # Пауза обработки
    result["processing_paused"] = Path(UPLOAD_PAUSE_DIRECTORY).exists()

    # Uptime
    started_at = getattr(app_state, STATE_STARTED_AT, None)
    result["started_at"] = started_at
    if started_at:
        try:
            started_dt = datetime.fromisoformat(started_at)
            result["uptime_seconds"] = int((datetime.now(timezone.utc) - started_dt).total_seconds())
        except Exception:
            result["uptime_seconds"] = None
    else:
        result["uptime_seconds"] = None

    # Общий статус
    if not db_ok:
        result["overall"] = "unhealthy"
    elif not result["db_watcher_running"] or not result["file_watcher_running"]:
        result["overall"] = "degraded"
    else:
        result["overall"] = "healthy"

    return result


def collect_disks() -> dict:
    """Раздел 2: состояние дисков с группировкой директорий по физическому диску."""

    # Список отслеживаемых директорий: (метка, путь)
    # data.db — директория, в которой лежит tlib.db
    db_dir = DATABASE_PATH.rsplit("/", 1)[0] if "/" in DATABASE_PATH else "data.db"
    monitored = [
        ("data.db",    db_dir),
        ("data.up",    UPLOAD_DIRECTORY),
        ("data.new",   UPLOAD_DONE_DIRECTORY),
        ("data.old",   BACKUP_DIRECTORY),
        ("data.cache", CACHE_DIRECTORY),
        ("logs",       LOG_DIRECTORY),
        ("data",       DATA_DIRECTORY),
    ]

    # Для каждой директории получаем device id и абсолютный путь
    dir_infos = []
    for label, raw_path in monitored:
        p = Path(raw_path)
        abs_path = str(p.resolve())
        try:
            dev_id = os.stat(abs_path if p.exists() else str(p)).st_dev
        except Exception:
            # Директория не существует — попробуем родителя
            try:
                dev_id = os.stat(str(p.parent)).st_dev
            except Exception:
                dev_id = -1
        dir_infos.append({
            "label": label,
            "raw_path": raw_path,
            "abs_path": abs_path,
            "dev_id": dev_id,
            "exists": p.exists(),
        })

    # Группируем по device id
    by_dev: dict[int, list] = defaultdict(list)
    for info in dir_infos:
        by_dev[info["dev_id"]].append(info)

    disks = []
    for dev_id, dirs in by_dev.items():
        # Берём первую существующую директорию для disk_usage и mountpoint
        sample_path = next((d["abs_path"] for d in dirs if Path(d["abs_path"]).exists()), None)
        if sample_path is None:
            sample_path = dirs[0]["abs_path"]

        try:
            usage = shutil.disk_usage(sample_path)
            total_bytes = usage.total
            used_bytes = usage.used
            free_bytes = usage.free
            used_pct = round(used_bytes / total_bytes * 100) if total_bytes else 0
            free_pct = round(free_bytes / total_bytes * 100) if total_bytes else 0
        except Exception:
            total_bytes = used_bytes = free_bytes = 0
            used_pct = 0
            free_pct = 0

        mountpoint = _find_mountpoint(Path(sample_path))

        # Размер каждой директории
        dir_rows = []
        for d in dirs:
            p = Path(d["raw_path"])
            if not p.exists():
                size_bytes = 0
            else:
                size_bytes = _dir_size_bytes(p)

            pct = round(size_bytes / total_bytes * 100) if total_bytes else 0
            dir_rows.append({
                "name": d["label"],
                "path": d["abs_path"],
                "size_mb": round(size_bytes / (1024 * 1024)),
                "size_bytes": size_bytes,
                "pct": pct,
                "exists": d["exists"],
                "cache_limit_mb": round(MAX_CACHE_SIZE / (1024 * 1024)) if d["label"] == "data.cache" else None,
            })

        disks.append({
            "mountpoint": mountpoint,
            "total_gb": round(total_bytes / (1024 ** 3)),
            "used_gb": round(used_bytes / (1024 ** 3)),
            "free_gb": round(free_bytes / (1024 ** 3)),
            "used_pct": used_pct,
            "free_pct": free_pct,
            "dirs": dir_rows,
        })

    return {"disks": disks}


def collect_growth() -> dict:
    """Раздел 3: динамика пополнения — объединяет бывшую database + growth."""
    result: dict = {}

    # Подсчёт файлов в data/
    data_dir = Path(DATA_DIRECTORY)
    if data_dir.exists():
        result["fs_json_count"] = sum(1 for p in data_dir.iterdir() if p.suffix.lower() == '.json')
        result["fs_archive_count"] = sum(1 for p in data_dir.iterdir() if p.suffix.lower() in ('.zip', '.pdf'))
    else:
        result["fs_json_count"] = 0
        result["fs_archive_count"] = 0

    # Файлы в очереди (только data.up/20_go/ — реальная точка входа pipeline)
    go_dir = Path(UPLOAD_GO_DIRECTORY)
    queue_count = sum(1 for p in go_dir.iterdir() if p.is_file()) if go_dir.exists() else 0
    result["queue_files"] = queue_count

    error_dir = Path(UPLOAD_ERROR_DIRECTORY)
    result["error_files"] = sum(1 for p in error_dir.iterdir() if p.is_file()) if error_dir.exists() else 0

    processing_dir = Path(UPLOAD_PROCESSING_DIRECTORY)
    result["processing_files"] = (
        sum(1 for p in processing_dir.iterdir() if p.is_file())
        if processing_dir.exists() else 0
    )

    done_dir = Path(UPLOAD_DONE_DIRECTORY)
    result["done_files"] = sum(1 for p in done_dir.iterdir() if p.is_file()) if done_dir.exists() else 0

    # SQL
    now_utc = datetime.now(timezone.utc)
    periods = {
        "added_24h": now_utc - timedelta(hours=24),
        "added_7d":  now_utc - timedelta(days=7),
        "added_30d": now_utc - timedelta(days=30),
    }

    try:
        conn = open_tlib_db()
        cur = conn.cursor()

        for key, since_dt in periods.items():
            cur.execute(
                f"SELECT COUNT(*) FROM {DATABASE_TABLE_NAME} WHERE ДатаВремяЗагрузки > ?",
                (since_dt.isoformat(),)
            )
            row = cur.fetchone()
            result[key] = row[0] if row else 0

        cur.execute(f"SELECT COUNT(*) FROM {DATABASE_TABLE_NAME}")
        result["db_total_count"] = cur.fetchone()[0]

        cur.execute(f"SELECT COUNT(*) FROM {DATABASE_TABLE_NAME} WHERE РазмерАрхива > 0")
        result["db_with_archive_count"] = cur.fetchone()[0]

        cur.execute(f"SELECT SUM(РазмерАрхива) FROM {DATABASE_TABLE_NAME}")
        row = cur.fetchone()
        result["total_archives_size_gb"] = round((row[0] or 0) / (1024 ** 3))

        cur.execute(
            f"SELECT COUNT(*) FROM {DATABASE_TABLE_NAME} WHERE РазмерАрхива = 0 OR РазмерАрхива IS NULL"
        )
        row = cur.fetchone()
        result["no_file_count"] = row[0] if row else 0

        cur.execute(
            f"SELECT Шифр, ДопШифр, Маршрут, Автор, ДатаВремяЗагрузки "
            f"FROM {DATABASE_TABLE_NAME} "
            f"ORDER BY ДатаВремяЗагрузки DESC LIMIT 10"
        )
        result["recent_reports"] = [
            {
                "id": r["Шифр"],
                "dop": r["ДопШифр"],
                "route": r["Маршрут"],
                "author": r["Автор"],
                "uploaded_at": r["ДатаВремяЗагрузки"],
            }
            for r in cur.fetchall()
        ]

        conn.close()
    except Exception as e:
        app_logger.error(f"[admin] Ошибка SQL в growth: {e}")
        for key in ("added_24h", "added_7d", "added_30d", "total_archives_size_gb", "no_file_count",
                    "db_total_count", "db_with_archive_count"):
            result.setdefault(key, None)
        result.setdefault("recent_reports", [])
        result["error"] = str(e)

    return result


def collect_traffic(app_state) -> dict:
    """Раздел 5: статистика посещений — читает из StatsCollector."""
    collector = getattr(app_state, "stats_collector", None)
    if collector is None:
        return {}
    try:
        return collector.query()
    except Exception as e:
        app_logger.error(f"[admin] Ошибка сбора traffic stats: {e}")
        return {}


def collect_security() -> dict:
    """Раздел 4: события безопасности за последние 24 часа."""
    log_path = Path(LOG_DIRECTORY) / LOG_FILE_CRITICAL
    if not log_path.exists():
        return {"total_events": 0, "by_type": {}, "top_ips": []}

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    type_counts: Counter = Counter()
    ip_counts: Counter = Counter()
    total = 0

    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "category=SECURITY" not in line:
                    continue
                ts_match = _LOG_TIMESTAMP_RE.match(line)
                if not ts_match:
                    continue
                try:
                    ts = datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if ts < cutoff:
                    continue

                kv = parse_logfmt_fields(line)
                event_type = kv.get("event_type", "UNKNOWN")
                ip = kv.get("ip", "")
                threat_level = kv.get("threat_level", "")

                type_counts[event_type] += 1
                total += 1

                if threat_level in ("HIGH", "MEDIUM") and ip and ip != "SYSTEM":
                    ip_counts[ip] += 1

    except Exception as e:
        app_logger.error(f"[admin] Ошибка парсинга critical.log: {e}")
        return {"total_events": 0, "by_type": {}, "top_ips": [], "error": str(e)}

    return {
        "total_events": total,
        "by_type": dict(type_counts),
        "top_ips": [{"ip": ip, "count": cnt} for ip, cnt in ip_counts.most_common(5)],
    }


def collect_status(app_state) -> dict:
    """Полный статус системы для /api/admin/status."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "health":   collect_health(app_state),
        "security": collect_security(),
        "disks":    collect_disks(),
        "growth":   collect_growth(),
        "traffic":  collect_traffic(app_state),
    }
