# Version 1.3 - 04.09.2026 14:25:00 GMT
# Ежедневный дайджест для администраторов TlibWebApp
# Описание: Сборка и отправка ежедневного email-дайджеста.
#           Парсит critical.log за 24ч, читает статистику из StatsCollector,
#           проверяет состояние очередей и диска.
#           Три секции: ТРЕБУЕТ ВНИМАНИЯ / СОБЫТИЯ / СТАТИСТИКА.
# 1.1: _FIELD_RE/_parse_log_fields -> parse_logfmt_fields из logging_config (единый парсер).
#           Отправляется раз в сутки в настраиваемое время (МСК).
#           Ежечасно проверяет диск и шлёт URGENT-алерт при критической занятости.
# 1.2: shutil.disk_usage заменён get_disk_usage из upload_io (единый helper расчёта диска).
# 1.3: тема дайджеста использует MAIL_SUBJECT_PREFIX (домен из SITE_URL) вместо хардкода "[tLib]".

import asyncio
import logging
import re
import smtplib
from collections import Counter
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from pathlib import Path

from config import (
    SMTP_SERVER, SMTP_PORT, SMTP_SENDER, SMTP_PASSWORD,
    LOG_DIRECTORY, LOG_FILE_CRITICAL,
    UPLOAD_ERROR_DIRECTORY, UPLOAD_GO_DIRECTORY,
    DATA_DIRECTORY,
    ALERT_DESCRIPTIONS, ALERT_LEVELS, MAIL_SUBJECT_PREFIX,
    DISK_WARN_PERCENT, DISK_CRIT_PERCENT,
    DIGEST_DEFAULT_SEND_TIME, DIGEST_TIMEZONE_OFFSET_HOURS,
    DIGEST_CHECK_INTERVAL_SECONDS, DISK_CHECK_INTERVAL_SECONDS,
    DIGEST_MARKER_FILE,
)
from logging_config import app_logger, parse_logfmt_fields
from services.alerts.alerter import send_admin_alert
from services.alerts.recipients import collect_admin_emails
from services.upload.upload_io import get_disk_usage

_log = logging.getLogger("tlibwebapp.alerts")

# Regex для парсинга строки critical.log в logfmt-формате:
# [2026-06-12 10:03:11.123] WARNING  [--------] func:42 | msg="..." event_type=X ip="Y" ...
_LOG_LINE_RE = re.compile(
    r"^\[(?P<ts>[^\]]+)\]\s+(?P<level>\w+)\s+.*?\|\s+(?P<fields>.+)$"
)


def parse_critical_log(hours: int = 24) -> list[dict]:
    """Читает critical.log и возвращает список записей за последние N часов.

    Каждая запись: {"ts": datetime, "level": str, "fields": dict}.
    Записи WARNING+ уровней. Молча пропускает непарсируемые строки.
    """
    log_path = Path(LOG_DIRECTORY) / LOG_FILE_CRITICAL
    if not log_path.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    records = []
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip()
                if not line:
                    continue
                m = _LOG_LINE_RE.match(line)
                if not m:
                    continue
                try:
                    ts = datetime.strptime(
                        m.group("ts")[:23], "%Y-%m-%d %H:%M:%S.%f"
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if ts < cutoff:
                    continue
                fields = parse_logfmt_fields(m.group("fields"))
                records.append({
                    "ts": ts,
                    "level": m.group("level"),
                    "fields": fields,
                })
    except Exception as e:
        _log.error(f"[digest] Ошибка чтения critical.log: {e}")
    return records


# ============================================================================
# СБОРЩИКИ СЕКЦИЙ
# ============================================================================

def _collect_attention_items(log_records: list[dict]) -> list[str]:
    """Формирует строки секции ТРЕБУЕТ ВНИМАНИЯ из записей лога и состояния папок."""
    items: list[str] = []

    # Агрегация WARNING/ERROR записей по event_type
    event_counts: Counter = Counter()
    ip_counts: Counter = Counter()
    for rec in log_records:
        level = rec["level"]
        if level not in ("WARNING", "ERROR"):
            continue
        fields = rec["fields"]
        event_type = fields.get("event_type", "")
        if not event_type:
            continue
        if ALERT_LEVELS.get(event_type, "ATTENTION") == "ATTENTION":
            event_counts[event_type] += 1
            ip = fields.get("ip", "")
            if ip and ip != "SYSTEM":
                ip_counts[ip] += 1

    for event_type, count in event_counts.most_common():
        human, _ = ALERT_DESCRIPTIONS.get(
            event_type,
            (f"Событие {event_type}", "")
        )
        items.append(f"- {human} — {count} раз(а).")

    # Топ IP по security-событиям
    if ip_counts:
        top_ip, top_cnt = ip_counts.most_common(1)[0]
        items.append(f"  Топ источник: IP {top_ip} ({top_cnt} событий).")

    # Файлы в 40_error
    error_dir = Path(UPLOAD_ERROR_DIRECTORY)
    if error_dir.exists():
        error_batches = set()
        for f in error_dir.iterdir():
            if f.is_file() and f.suffix not in (".log",):
                error_batches.add(f.stem)
        if error_batches:
            items.append(
                f"- {len(error_batches)} отчёт(ов) не прошли обработку и ждут в папке ошибок. "
                "Авторы ждут публикации — зайдите в панель загрузки и разберите."
            )

    # Файлы в 20_go старше суток
    go_dir = Path(UPLOAD_GO_DIRECTORY)
    if go_dir.exists():
        stale = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        for f in go_dir.iterdir():
            if f.is_file():
                try:
                    mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
                    if mtime < cutoff:
                        stale.append(f.name)
                except OSError:
                    pass
        if stale:
            items.append(
                f"- {len(stale)} файл(ов) в очереди обработки лежат более суток — "
                "возможно File Watcher завис или стоит на паузе."
            )

    # Диск > DISK_WARN_PERCENT
    try:
        usage = get_disk_usage(DATA_DIRECTORY)
        pct = usage["used_pct"]
        if pct >= DISK_WARN_PERCENT:
            free_gb = usage["free_gb"]
            items.append(
                f"- Диск заполнен на {pct}% (свободно {free_gb:.1f} ГБ). "
                "Планируйте освобождение места."
            )
    except Exception:
        pass

    return items


def _collect_events_items() -> list[str]:
    """Формирует строки секции СОБЫТИЯ."""
    items: list[str] = []
    try:
        go_dir = Path(UPLOAD_GO_DIRECTORY)
        go_count = sum(1 for f in go_dir.iterdir() if f.is_file()) if go_dir.exists() else 0
        items.append(f"- Файлов в очереди обработки (20_go): {go_count}.")
    except Exception:
        pass
    try:
        error_dir = Path(UPLOAD_ERROR_DIRECTORY)
        err_count = sum(1 for f in error_dir.iterdir() if f.is_file()) if error_dir.exists() else 0
        if err_count == 0:
            items.append("- Папка ошибок обработки пуста.")
    except Exception:
        pass
    return items


def _collect_stats_items(stats_collector=None) -> list[str]:
    """Формирует строки секции СТАТИСТИКА из StatsCollector и диска."""
    items: list[str] = []

    if stats_collector is not None:
        try:
            q = stats_collector.query()
            h24 = q.get("h24", {})
            ips = h24.get("unique_ips", 0)
            searches = h24.get("search", 0)
            downloads = h24.get("download", 0)
            err4 = h24.get("errors_4xx", 0)
            err5 = h24.get("errors_5xx", 0)
            items.append(f"- Уникальных посетителей за сутки: {ips}.")
            items.append(f"- Поисковых запросов: {searches}, скачиваний: {downloads}.")
            if err4 or err5:
                items.append(f"- Ошибок сервера: {err4} (4xx) + {err5} (5xx).")

            top_views = q.get("top_report_views", [])[:3]
            if top_views:
                top_str = ", ".join(
                    f"{r['report_id']} ({r['unique_ips']} просм.)"
                    for r in top_views
                )
                items.append(f"- Топ отчётов по просмотрам: {top_str}.")
        except Exception as e:
            _log.error(f"[digest] Ошибка сбора статистики: {e}")

    # Диск — всегда показываем
    try:
        usage = get_disk_usage(DATA_DIRECTORY)
        pct = usage["used_pct"]
        total_gb = usage["total_gb"]
        free_gb = usage["free_gb"]
        items.append(f"- Диск: занято {pct}% от {total_gb:.0f} ГБ, свободно {free_gb:.1f} ГБ.")
    except Exception:
        pass

    return items


def _collect_technical_details(log_records: list[dict]) -> str:
    """Формирует блок технических деталей из счётчиков event_type в логе."""
    counts: Counter = Counter()
    for rec in log_records:
        if rec["level"] in ("WARNING", "ERROR"):
            et = rec["fields"].get("event_type", "")
            if et:
                counts[et] += 1
    if not counts:
        return ""
    lines = ["--- Технические детали ---"]
    for et, cnt in counts.most_common():
        lines.append(f"  {et}: {cnt}")
    return "\n".join(lines)


# ============================================================================
# СБОРКА И ОТПРАВКА ДАЙДЖЕСТА
# ============================================================================

def build_digest(stats_collector=None) -> tuple[str, str]:
    """Собирает дайджест и возвращает (subject, body) для отправки."""
    now = datetime.now(timezone.utc) + timedelta(hours=DIGEST_TIMEZONE_OFFSET_HOURS)
    date_str = now.strftime("%d.%m.%Y")

    log_records = parse_critical_log(hours=24)

    attention_items = _collect_attention_items(log_records)
    events_items = _collect_events_items()
    stats_items = _collect_stats_items(stats_collector)
    tech_details = _collect_technical_details(log_records) if attention_items else ""

    # Тема
    if attention_items:
        problem_count = sum(1 for i in attention_items if i.startswith("- "))
        subject = f"{MAIL_SUBJECT_PREFIX} Дайджест {date_str} — {problem_count} проблем(ы) требуют внимания"
    else:
        subject = f"{MAIL_SUBJECT_PREFIX} Дайджест {date_str} — всё в порядке"

    # Тело
    sections: list[str] = [f"tLib дайджест {date_str}"]
    sections.append("")

    if attention_items:
        sections.append("ТРЕБУЕТ ВНИМАНИЯ")
        sections.extend(attention_items)
        sections.append("")

    if events_items:
        sections.append("СОБЫТИЯ")
        sections.extend(events_items)
        sections.append("")

    if stats_items:
        sections.append("СТАТИСТИКА")
        sections.extend(stats_items)

    if tech_details:
        sections.append("")
        sections.append(tech_details)

    body = "\n".join(sections)
    return subject, body


def _send_digest_sync(stats_collector=None) -> None:
    """Собирает и отправляет дайджест синхронно. Вызывается из asyncio.to_thread."""
    if not SMTP_SENDER or not SMTP_PASSWORD:
        _log.error("[digest] SMTP credentials не настроены — дайджест не отправлен")
        return

    recipients = collect_admin_emails()
    if not recipients:
        _log.warning("[digest] Нет адресов администраторов — дайджест не отправлен")
        return

    subject, body = build_digest(stats_collector)

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = SMTP_SENDER
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as srv:
            srv.starttls()
            srv.login(SMTP_SENDER, SMTP_PASSWORD)
            srv.sendmail(SMTP_SENDER, recipients, msg.as_string())
        app_logger.info(f"[digest] Дайджест отправлен {len(recipients)} адм.: {subject}")
    except Exception as e:
        _log.error(f"[digest] SMTP ошибка при отправке дайджеста: {e}")


# ============================================================================
# ФОНОВАЯ ЗАДАЧА
# ============================================================================

def _get_digest_send_time() -> tuple[int, int]:
    """Читает время отправки дайджеста из app_settings. Возвращает (hour, minute) по МСК."""
    try:
        from services.auth.auth_db import get_setting
        val = get_setting("digest_send_time", DIGEST_DEFAULT_SEND_TIME)
        parts = val.split(":")
        return int(parts[0]), int(parts[1])
    except Exception:
        default = DIGEST_DEFAULT_SEND_TIME.split(":")
        return int(default[0]), int(default[1])


def _should_send_today(hour: int, minute: int) -> bool:
    """Возвращает True если сейчас время отправки и дайджест ещё не отправлялся сегодня."""
    now_msk = datetime.now(timezone.utc) + timedelta(hours=DIGEST_TIMEZONE_OFFSET_HOURS)
    if now_msk.hour != hour or now_msk.minute != minute:
        return False

    marker = Path(DIGEST_MARKER_FILE)
    today = now_msk.strftime("%Y-%m-%d")
    if marker.exists():
        try:
            last = marker.read_text(encoding="utf-8").strip()
            if last == today:
                return False
        except Exception:
            pass
    return True


def _mark_sent() -> None:
    """Записывает сегодняшнюю дату в маркер-файл."""
    try:
        now_msk = datetime.now(timezone.utc) + timedelta(hours=DIGEST_TIMEZONE_OFFSET_HOURS)
        today = now_msk.strftime("%Y-%m-%d")
        marker = Path(DIGEST_MARKER_FILE)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(today, encoding="utf-8")
    except Exception as e:
        _log.error(f"[digest] Не удалось обновить маркер дайджеста: {e}")


def _check_disk_and_alert() -> None:
    """Проверяет занятость диска и шлёт URGENT-алерт если выше критического порога."""
    try:
        usage = get_disk_usage(DATA_DIRECTORY)
        pct = usage["used_pct"]
        if pct >= DISK_CRIT_PERCENT:
            free_gb = usage["free_gb"]
            send_admin_alert(
                "DISK_LOW",
                disk_used_pct=pct,
                disk_free_gb=round(free_gb, 1),
            )
    except Exception as e:
        _log.error(f"[digest] Ошибка проверки диска: {e}")


async def daily_digest_task(app) -> None:
    """Фоновая задача: ежеминутная проверка «пора ли», ежечасная проверка диска.

    Запускается в lifespan через asyncio.create_task(daily_digest_task(app)).
    """
    app_logger.info("[digest] Задача дайджеста запущена")
    disk_check_counter = 0

    while True:
        try:
            await asyncio.sleep(DIGEST_CHECK_INTERVAL_SECONDS)

            # Ежечасная проверка диска
            disk_check_counter += DIGEST_CHECK_INTERVAL_SECONDS
            if disk_check_counter >= DISK_CHECK_INTERVAL_SECONDS:
                disk_check_counter = 0
                await asyncio.to_thread(_check_disk_and_alert)

            # Проверка времени отправки дайджеста
            hour, minute = _get_digest_send_time()
            if _should_send_today(hour, minute):
                _mark_sent()
                stats_collector = getattr(getattr(app, "state", None), "stats_collector", None)
                await asyncio.to_thread(_send_digest_sync, stats_collector)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            app_logger.error(f"[digest] Ошибка в daily_digest_task: {e}")
