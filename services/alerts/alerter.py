# Version 1.0 - 12.06.2026 14:00:00 GMT
# Модуль срочных email-алертов для администраторов TlibWebApp
# Описание: Немедленные URGENT-уведомления с троттлингом.
#           send_admin_alert() — отправить алерт с человеческим описанием + техдетали.
#           CriticalMailHandler — logging.Handler для автоматической отправки при CRITICAL.
#           Троттлинг in-memory: не более 1 письма на тип события за ALERT_THROTTLE_MINUTES.
#           Счётчик security-событий: при >SECURITY_STORM_THRESHOLD за окно → URGENT «атака».
#           Все SMTP-ошибки глотаются с записью в лог — алертер никогда не роняет приложение.

import logging
import smtplib
import threading
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText

from config import (
    SMTP_SERVER, SMTP_PORT, SMTP_SENDER, SMTP_PASSWORD,
    ALERT_THROTTLE_MINUTES, SECURITY_STORM_THRESHOLD,
    ALERT_LEVELS, ALERT_DESCRIPTIONS,
)
from services.alerts.recipients import collect_admin_emails

# Логгер для ошибок самого алертера — не выше ERROR (защита от рекурсии через CriticalMailHandler)
_log = logging.getLogger("tlibwebapp.alerts")

# ============================================================================
# СОСТОЯНИЕ ТРОТТЛИНГА (in-memory, сбрасывается при рестарте)
# ============================================================================

# dict[event_type] -> (last_sent: datetime, suppressed_count: int)
_throttle: dict[str, tuple[datetime, int]] = {}
_throttle_lock = threading.Lock()

# Счётчик security-событий за текущее окно
_security_count: int = 0
_security_window_start: datetime = datetime.now(timezone.utc)
_security_lock = threading.Lock()


def _is_throttled(event_type: str) -> tuple[bool, int]:
    """Проверяет троттлинг. Возвращает (throttled, suppressed_count)."""
    now = datetime.now(timezone.utc)
    window = timedelta(minutes=ALERT_THROTTLE_MINUTES)
    with _throttle_lock:
        entry = _throttle.get(event_type)
        if entry is None:
            _throttle[event_type] = (now, 0)
            return False, 0
        last_sent, suppressed = entry
        if now - last_sent < window:
            _throttle[event_type] = (last_sent, suppressed + 1)
            return True, suppressed + 1
        # Окно истекло: сбрасываем и разрешаем отправку
        count = suppressed
        _throttle[event_type] = (now, 0)
        return False, count


def _check_security_storm() -> bool:
    """Возвращает True если порог security-событий за окно превышен.

    Сбрасывает счётчик при истечении окна; инкрементирует и проверяет при каждом вызове.
    """
    global _security_count, _security_window_start
    now = datetime.now(timezone.utc)
    window = timedelta(minutes=ALERT_THROTTLE_MINUTES)
    with _security_lock:
        if now - _security_window_start >= window:
            _security_count = 0
            _security_window_start = now
        _security_count += 1
        return _security_count >= SECURITY_STORM_THRESHOLD


def _build_body(event_type: str, suppressed: int, **data) -> tuple[str, str]:
    """Формирует тему и тело письма.

    Структура тела:
        <человеческое описание>
        Что делать: <рекомендация>
        [При необходимости: сколько раз повторялось]

        --- Технические детали ---
        <logfmt-поля>
    """
    human, action = ALERT_DESCRIPTIONS.get(
        event_type,
        (
            f"Зарегистрирована ошибка типа {event_type}.",
            "Подробности см. в технических деталях ниже.",
        ),
    )

    level = ALERT_LEVELS.get(event_type, "URGENT")
    subject = f"[tLib] {'СРОЧНО: ' if level == 'URGENT' else ''}{human[:80]}"

    lines = [human, f"Что делать: {action}"]
    if suppressed:
        lines.append(f"Событие повторилось ещё {suppressed} раз(а) с прошлого уведомления.")

    if data:
        lines.append("")
        lines.append("--- Технические детали ---")
        lines.append(f"event_type={event_type}")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines.append(f"time={ts}")
        for k, v in data.items():
            if isinstance(v, str):
                v_fmt = f'"{v}"'
            else:
                v_fmt = str(v)
            lines.append(f"{k}={v_fmt}")

    body = "\n".join(lines)
    return subject, body


def _send_sync(subject: str, body: str, recipients: list[str]) -> None:
    """Отправляет письмо синхронно. Вызывается в отдельном потоке."""
    if not SMTP_SENDER or not SMTP_PASSWORD:
        _log.error("[alerts] SMTP credentials не настроены — письмо не отправлено")
        return
    if not recipients:
        _log.warning("[alerts] Нет адресов администраторов — письмо не отправлено")
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = SMTP_SENDER
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as srv:
            srv.starttls()
            srv.login(SMTP_SENDER, SMTP_PASSWORD)
            srv.sendmail(SMTP_SENDER, recipients, msg.as_string())
        _log.info(f"[alerts] Алерт '{subject}' отправлен {len(recipients)} адм.")
    except Exception as e:
        # Ошибки отправки логируются не выше ERROR — не вызываем CriticalMailHandler рекурсивно
        _log.error(f"[alerts] SMTP ошибка при отправке алерта '{subject}': {e}")


def send_admin_alert(event_type: str, **data) -> None:
    """Отправляет URGENT-алерт администраторам с троттлингом.

    Если событие — security-типа, дополнительно проверяет порог шторма
    и при превышении инициирует URGENT-письмо SECURITY_STORM.

    Аргументы:
        event_type: ключ события (из ALERT_LEVELS / ALERT_DESCRIPTIONS)
        **data:     произвольные поля для блока «технические детали»

    Работает асинхронно: SMTP-отправка уходит в отдельный поток.
    Все исключения глотаются — не роняет приложение.
    """
    try:
        # Проверка security-шторма
        security_types = {"PATH_TRAVERSAL_ATTEMPT", "RATE_LIMIT_EXCEEDED",
                          "ZIP_BOMB_DETECTED", "ARCHIVE_SIZE_EXCEEDED", "INVALID_REQUEST"}
        if event_type in security_types:
            if _check_security_storm():
                _fire_security_storm()

        # Пропускаем не-URGENT без проверки троттлинга (они идут в дайджест)
        level = ALERT_LEVELS.get(event_type, "URGENT")
        if level != "URGENT":
            return

        throttled, suppressed = _is_throttled(event_type)
        if throttled:
            return

        recipients = collect_admin_emails()
        subject, body = _build_body(event_type, suppressed, **data)

        t = threading.Thread(target=_send_sync, args=(subject, body, recipients), daemon=True)
        t.start()

    except Exception as e:
        _log.error(f"[alerts] Ошибка в send_admin_alert({event_type}): {e}")


def send_admin_alert_direct(subject: str, body: str) -> None:
    """Отправляет письмо с произвольным заголовком и телом без троттлинга.

    Используется для тестовых писем и SERVER_STARTED (где нужен точный текст).
    """
    try:
        recipients = collect_admin_emails()
        t = threading.Thread(target=_send_sync, args=(subject, body, recipients), daemon=True)
        t.start()
    except Exception as e:
        _log.error(f"[alerts] Ошибка в send_admin_alert_direct: {e}")


def _fire_security_storm() -> None:
    """Отправляет URGENT-алерт о security-шторме (однократно за окно через троттлинг)."""
    throttled, suppressed = _is_throttled("SECURITY_STORM")
    if throttled:
        return
    recipients = collect_admin_emails()
    subject, body = _build_body(
        "SECURITY_STORM", suppressed,
        threshold=SECURITY_STORM_THRESHOLD,
        window_minutes=ALERT_THROTTLE_MINUTES,
    )
    t = threading.Thread(target=_send_sync, args=(subject, body, recipients), daemon=True)
    t.start()


# ============================================================================
# LOGGING HANDLER ДЛЯ УРОВНЯ CRITICAL
# ============================================================================

class CriticalMailHandler(logging.Handler):
    """Logging handler: при каждом CRITICAL-записи отправляет URGENT-письмо администраторам.

    Устанавливается в logging_config.py с уровнем CRITICAL.
    Защита от рекурсии: ошибки отправки логируются через _log (не выше ERROR).
    """

    def __init__(self):
        super().__init__(level=logging.CRITICAL)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Определяем event_type из extra_data или по умолчанию
            extra = getattr(record, "extra_data", {}) or {}
            event_type = extra.get("event_type", "CRITICAL_ERROR")

            # Форматируем технические детали из записи лога
            data: dict = dict(extra)
            data["logger"] = record.name
            data["function"] = f"{record.funcName}:{record.lineno}"
            if record.exc_info:
                data["exception_type"] = record.exc_info[0].__name__
                import traceback
                tb = traceback.format_exception(*record.exc_info)
                data["traceback"] = " | ".join(
                    line.strip() for line in "".join(tb).splitlines() if line.strip()
                )[:500]

            throttled, suppressed = _is_throttled(event_type)
            if throttled:
                return

            recipients = collect_admin_emails()
            subject, body = _build_body(event_type, suppressed, **data)

            t = threading.Thread(
                target=_send_sync, args=(subject, body, recipients), daemon=True
            )
            t.start()
        except Exception as e:
            _log.error(f"[alerts] CriticalMailHandler.emit ошибка: {e}")
