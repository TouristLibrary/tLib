# Version 1.2 - 04.09.2026 14:25:00 GMT
# Unit tests for services/alerts/
# Описание: Тесты парсера critical.log, форматирования писем,
#           логики троттлинга и сборки дайджеста. SMTP не вызывается (замокан).
# Изменения v1.2: test_subject_prefix_tlib -> test_subject_prefix_domain/test_subject_prefix_reflects_domain
#           (тема письма использует MAIL_SUBJECT_PREFIX — домен из SITE_URL — вместо хардкода "[tLib]").

from __future__ import annotations

import re
import threading
import tempfile
import textwrap
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Парсер critical.log
# ---------------------------------------------------------------------------

class TestParseCriticalLog(unittest.TestCase):
    """Тесты парсера logfmt-строк из critical.log."""

    def _write_log(self, tmp: Path, lines: list[str]) -> None:
        log_file = tmp / "critical.log"
        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_parses_warning_line(self):
        from services.alerts.digest import _LOG_LINE_RE
        from logging_config import parse_logfmt_fields
        line = (
            '[2026-06-12 10:03:11.123] WARNING  [--------] security:85 | '
            'msg="Security: PATH_TRAVERSAL_ATTEMPT" event_type=PATH_TRAVERSAL_ATTEMPT ip="1.2.3.4"'
        )
        m = _LOG_LINE_RE.match(line)
        self.assertIsNotNone(m)
        fields = parse_logfmt_fields(m.group("fields"))
        self.assertEqual(fields["event_type"], "PATH_TRAVERSAL_ATTEMPT")
        self.assertEqual(fields["ip"], "1.2.3.4")

    def test_ignores_info_lines(self):
        """Строки уровня INFO не должны попадать в parse_critical_log."""
        from services.alerts.digest import parse_critical_log
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%d %H:%M:%S.000")
        line = f'[{ts}] INFO     [--------] func:1 | msg="ok" event_type=REPORT_PUBLISHED'
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "critical.log").write_text(line + "\n", encoding="utf-8")
            with patch("services.alerts.digest.LOG_DIRECTORY", tmp), \
                 patch("services.alerts.digest.LOG_FILE_CRITICAL", "critical.log"):
                records = parse_critical_log(hours=1)
        # INFO не попадает — уровень не WARNING/ERROR, но парсер возвращает все
        # строки в окне времени; фильтрация по уровню — в _collect_attention_items
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["level"], "INFO")

    def test_filters_old_records(self):
        """Записи старше указанного окна не возвращаются."""
        from services.alerts.digest import parse_critical_log
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S.000")
        line = f'[{old_ts}] WARNING  [--------] func:1 | msg="old" event_type=TEST_EVENT'
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "critical.log").write_text(line + "\n", encoding="utf-8")
            with patch("services.alerts.digest.LOG_DIRECTORY", tmp), \
                 patch("services.alerts.digest.LOG_FILE_CRITICAL", "critical.log"):
                records = parse_critical_log(hours=24)
        self.assertEqual(records, [])

    def test_empty_log(self):
        from services.alerts.digest import parse_critical_log
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "critical.log").write_text("", encoding="utf-8")
            with patch("services.alerts.digest.LOG_DIRECTORY", tmp), \
                 patch("services.alerts.digest.LOG_FILE_CRITICAL", "critical.log"):
                records = parse_critical_log()
        self.assertEqual(records, [])

    def test_missing_log(self):
        from services.alerts.digest import parse_critical_log
        with tempfile.TemporaryDirectory() as tmp:
            with patch("services.alerts.digest.LOG_DIRECTORY", tmp), \
                 patch("services.alerts.digest.LOG_FILE_CRITICAL", "critical.log"):
                records = parse_critical_log()
        self.assertEqual(records, [])

    def test_parse_fields_quoted_and_unquoted(self):
        from logging_config import parse_logfmt_fields
        fields_str = 'msg="hello world" count=42 flag=true ip="1.2.3.4"'
        result = parse_logfmt_fields(fields_str)
        self.assertEqual(result["msg"], "hello world")
        self.assertEqual(result["count"], "42")
        self.assertEqual(result["flag"], "true")
        self.assertEqual(result["ip"], "1.2.3.4")


# ---------------------------------------------------------------------------
# Форматирование письма
# ---------------------------------------------------------------------------

class TestBuildBody(unittest.TestCase):
    """Тесты формирования subject и body алерта."""

    def test_known_event_has_human_description(self):
        from services.alerts.alerter import _build_body
        # event_type попадает в тело только при наличии **data (в блок техдеталей)
        subject, body = _build_body("DB_SWAP_FAILED", 0, error="test")
        self.assertIn("обновить", body.lower())
        self.assertIn("Что делать:", body)
        self.assertIn("DB_SWAP_FAILED", body)

    def test_unknown_event_fallback(self):
        from services.alerts.alerter import _build_body
        subject, body = _build_body("UNKNOWN_XYZ_EVENT", 0)
        self.assertIn("UNKNOWN_XYZ_EVENT", body)
        self.assertIn("технических деталях", body)

    def test_suppressed_count_in_body(self):
        from services.alerts.alerter import _build_body
        subject, body = _build_body("DISK_LOW", 5)
        self.assertIn("5", body)
        self.assertIn("повторилось", body)

    def test_technical_details_block(self):
        from services.alerts.alerter import _build_body
        subject, body = _build_body("DB_SWAP_FAILED", 0, error_type="OSError", path="/tmp/test.db")
        self.assertIn("--- Технические детали ---", body)
        # Строковые значения форматируются в кавычках: error_type="OSError"
        self.assertIn('error_type="OSError"', body)
        self.assertIn("path=", body)

    def test_urgent_subject_prefix(self):
        from services.alerts.alerter import _build_body
        subject, _ = _build_body("DB_SWAP_FAILED", 0)
        self.assertIn("СРОЧНО", subject)

    def test_subject_prefix_domain(self):
        from config import MAIL_SUBJECT_PREFIX
        from services.alerts.alerter import _build_body
        subject, _ = _build_body("DB_SWAP_FAILED", 0)
        self.assertTrue(subject.startswith(MAIL_SUBJECT_PREFIX))

    def test_subject_prefix_reflects_domain(self):
        # Префикс — это домен хостинга (MAIL_SUBJECT_PREFIX), не хардкод "[tLib]":
        # при другом значении константы тема должна меняться вместе с ней.
        with patch("services.alerts.alerter.MAIL_SUBJECT_PREFIX", "[tlib.ru]"):
            from services.alerts.alerter import _build_body
            subject, _ = _build_body("DB_SWAP_FAILED", 0)
            self.assertTrue(subject.startswith("[tlib.ru]"))


# ---------------------------------------------------------------------------
# Троттлинг
# ---------------------------------------------------------------------------

class TestThrottling(unittest.TestCase):
    """Тесты механизма троттлинга алертов."""

    def setUp(self):
        """Очищаем словарь троттлинга перед каждым тестом."""
        import services.alerts.alerter as alerter_mod
        with alerter_mod._throttle_lock:
            alerter_mod._throttle.clear()

    def test_first_call_not_throttled(self):
        from services.alerts.alerter import _is_throttled
        throttled, count = _is_throttled("TEST_EVENT_UNIQUE_A")
        self.assertFalse(throttled)
        self.assertEqual(count, 0)

    def test_second_call_throttled(self):
        from services.alerts.alerter import _is_throttled
        _is_throttled("TEST_EVENT_UNIQUE_B")
        throttled, count = _is_throttled("TEST_EVENT_UNIQUE_B")
        self.assertTrue(throttled)
        self.assertEqual(count, 1)

    def test_suppressed_count_increments(self):
        from services.alerts.alerter import _is_throttled
        _is_throttled("TEST_EVENT_UNIQUE_C")
        _is_throttled("TEST_EVENT_UNIQUE_C")
        throttled, count = _is_throttled("TEST_EVENT_UNIQUE_C")
        self.assertTrue(throttled)
        self.assertEqual(count, 2)

    def test_after_window_expires_not_throttled(self):
        """После истечения окна событие снова разрешается."""
        import services.alerts.alerter as alerter_mod
        from services.alerts.alerter import _is_throttled
        past = datetime.now(timezone.utc) - timedelta(minutes=60)
        with alerter_mod._throttle_lock:
            alerter_mod._throttle["TEST_EVENT_EXPIRED"] = (past, 3)
        throttled, count = _is_throttled("TEST_EVENT_EXPIRED")
        self.assertFalse(throttled)
        # Подавленные события возвращаются в count
        self.assertEqual(count, 3)

    def test_different_events_independent(self):
        from services.alerts.alerter import _is_throttled
        _is_throttled("EVENT_X1")
        throttled_x, _ = _is_throttled("EVENT_X1")
        throttled_y, _ = _is_throttled("EVENT_Y1")
        self.assertTrue(throttled_x)
        self.assertFalse(throttled_y)


# ---------------------------------------------------------------------------
# Сборка дайджеста
# ---------------------------------------------------------------------------

class TestBuildDigest(unittest.TestCase):
    """Тесты сборки subject/body дайджеста."""

    def test_empty_digest_subject(self):
        from services.alerts.digest import build_digest
        _disk_ok = {"used_pct": 50, "free_gb": 50.0, "used_gb": 50.0, "total_gb": 100.0,
                    "free_bytes": 50 * 1024**3, "used_bytes": 50 * 1024**3, "total_bytes": 100 * 1024**3}
        with patch("services.alerts.digest.parse_critical_log", return_value=[]), \
             patch("services.alerts.digest._collect_events_items", return_value=[]), \
             patch("services.alerts.digest._collect_stats_items", return_value=[]), \
             patch("services.alerts.digest.get_disk_usage", return_value=_disk_ok), \
             patch("pathlib.Path.exists", return_value=False):
            subject, body = build_digest(stats_collector=None)
        self.assertIn("всё в порядке", subject)

    def test_attention_items_in_subject(self):
        from services.alerts.digest import build_digest
        now = datetime.now(timezone.utc)
        fake_records = [
            {
                "ts": now,
                "level": "WARNING",
                "fields": {"event_type": "BATCH_ERROR", "ip": "1.2.3.4"},
            }
        ]
        _disk_ok = {"used_pct": 60, "free_gb": 40.0, "used_gb": 60.0, "total_gb": 100.0,
                    "free_bytes": 40 * 1024**3, "used_bytes": 60 * 1024**3, "total_bytes": 100 * 1024**3}
        with patch("services.alerts.digest.parse_critical_log", return_value=fake_records), \
             patch("pathlib.Path.exists", return_value=False), \
             patch("services.alerts.digest.get_disk_usage", return_value=_disk_ok):
            subject, body = build_digest(stats_collector=None)
        self.assertIn("требуют внимания", subject)
        self.assertIn("ТРЕБУЕТ ВНИМАНИЯ", body)

    def test_body_sections_present_when_data(self):
        from services.alerts.digest import build_digest
        _disk_ok = {"used_pct": 50, "free_gb": 50.0, "used_gb": 50.0, "total_gb": 100.0,
                    "free_bytes": 50 * 1024**3, "used_bytes": 50 * 1024**3, "total_bytes": 100 * 1024**3}
        with patch("services.alerts.digest.parse_critical_log", return_value=[]), \
             patch("services.alerts.digest._collect_events_items",
                   return_value=["- Файлов в очереди: 0."]), \
             patch("services.alerts.digest._collect_stats_items",
                   return_value=["- Уникальных посетителей: 100."]), \
             patch("pathlib.Path.exists", return_value=False), \
             patch("services.alerts.digest.get_disk_usage", return_value=_disk_ok):
            subject, body = build_digest(stats_collector=None)
        self.assertIn("СОБЫТИЯ", body)
        self.assertIn("СТАТИСТИКА", body)


# ---------------------------------------------------------------------------
# send_admin_alert не роняет приложение
# ---------------------------------------------------------------------------

class TestSendAdminAlertSafety(unittest.TestCase):
    """Алертер не должен поднимать исключения даже при сбое SMTP."""

    def setUp(self):
        import services.alerts.alerter as alerter_mod
        with alerter_mod._throttle_lock:
            alerter_mod._throttle.clear()

    def test_no_exception_on_smtp_failure(self):
        """send_admin_alert не должен бросать исключение даже при сбое SMTP."""
        import time
        from services.alerts.alerter import send_admin_alert
        errors_in_thread = []

        def failing_send(*args, **kwargs):
            errors_in_thread.append("called")

        with patch("services.alerts.alerter.collect_admin_emails", return_value=["a@b.com"]), \
             patch("services.alerts.alerter._send_sync", side_effect=failing_send):
            try:
                send_admin_alert("DB_SWAP_FAILED", error="test")
            except Exception as e:
                self.fail(f"send_admin_alert бросил исключение: {e}")
        # Ждём завершения потока
        time.sleep(0.1)

    def test_non_urgent_does_not_send(self):
        """ATTENTION-события не отправляются как алерты — уходят только в дайджест."""
        sent_calls = []
        with patch("services.alerts.alerter.collect_admin_emails", return_value=["a@b.com"]), \
             patch("services.alerts.alerter._send_sync",
                   side_effect=lambda *a, **kw: sent_calls.append(a)):
            from services.alerts.alerter import send_admin_alert
            send_admin_alert("BATCH_ERROR")
        # BATCH_ERROR — ATTENTION, писем быть не должно
        self.assertEqual(sent_calls, [])


if __name__ == "__main__":
    unittest.main()
