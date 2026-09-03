# Version 1.0 - 14.05.2026 00:00:00 GMT
# Unit tests for services/file_watcher/deleter.py
# Описание: Проверяет контракт process_delete_operation() после рефакторинга на единый
#           канал записи в БД. Функция больше не трогает БД — только переносит файлы
#           из data/ в data.old/ и инвалидирует кеш архива.
#
# Контракт:
#   - (True,  N>0, "")  — N файлов перенесено, пересборка БД нужна
#   - (True,  0,   "")  — файлов в data/ не было, идемпотентный no-op (пересборка не нужна)
#   - (False, 0,  msg) — ошибка бэкапа

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestProcessDeleteOperation(unittest.TestCase):
    """
    Тесты для process_delete_operation().

    Функция работает с DATA_DIRECTORY из конфига; мы патчим константу,
    чтобы тесты не зависели от реальной файловой системы проекта.
    """

    def _run(self, data_dir: Path, group_id: str, shifr: str, dopshifr=None):
        """Запускает process_delete_operation с подменённым DATA_DIRECTORY."""
        with patch("services.file_watcher.deleter.DATA_DIRECTORY", str(data_dir)):
            from services.file_watcher import deleter
            # Перезагружаем DATA_DIRECTORY внутри функции через патч модульного атрибута
            return deleter.process_delete_operation(group_id, shifr, dopshifr)

    # ------------------------------------------------------------------
    # No-op: файлов в data/ нет
    # ------------------------------------------------------------------

    def test_no_files_returns_success_zero_moved(self):
        """Если в data/ нет файлов группы — success=True, files_moved=0 (идемпотентный no-op)."""
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            success, files_moved, error_msg = self._run(data_dir, "00001", "1")
            self.assertTrue(success, f"Ожидали success=True, получили: {error_msg}")
            self.assertEqual(files_moved, 0)
            self.assertEqual(error_msg, "")

    # ------------------------------------------------------------------
    # Нормальный сценарий: файлы есть, бэкап успешен
    # ------------------------------------------------------------------

    def test_files_present_returns_files_moved_count(self):
        """Если файлы найдены и перенесены — success=True, files_moved == количество файлов."""
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            # Создаём два файла группы 00001
            (data_dir / "00001.json").write_text("{}", encoding="utf-8")
            (data_dir / "00001.pdf").write_bytes(b"%PDF")

            # Мокаем backup_to_old (перемещение в data.old/) и invalidate_archive_cache
            with patch("services.file_watcher.deleter.backup_to_old", return_value=True) as mock_backup, \
                 patch("services.file_watcher.deleter.invalidate_archive_cache", return_value=0):
                success, files_moved, error_msg = self._run(data_dir, "00001", "1")

            self.assertTrue(success, f"Ожидали success=True, получили: {error_msg}")
            self.assertEqual(files_moved, 2)
            self.assertEqual(error_msg, "")
            mock_backup.assert_called_once()

    def test_dopshifr_group_found(self):
        """Файлы группы с ДопШифр (например 00012-FRT) должны находиться и переноситься."""
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            (data_dir / "00012-FRT.json").write_text("{}", encoding="utf-8")

            with patch("services.file_watcher.deleter.backup_to_old", return_value=True), \
                 patch("services.file_watcher.deleter.invalidate_archive_cache", return_value=0):
                success, files_moved, error_msg = self._run(data_dir, "00012-FRT", "12", "FRT")

            self.assertTrue(success)
            self.assertEqual(files_moved, 1)

    def test_unnormalized_group_id_resolved_to_5digits(self):
        """group_id '12-FRT' должен нормализоваться до '00012-FRT' при поиске файлов."""
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            # Файл лежит под нормализованным именем
            (data_dir / "00012-FRT.json").write_text("{}", encoding="utf-8")

            with patch("services.file_watcher.deleter.backup_to_old", return_value=True), \
                 patch("services.file_watcher.deleter.invalidate_archive_cache", return_value=0):
                # Передаём ненормализованный group_id
                success, files_moved, error_msg = self._run(data_dir, "12-FRT", "12", "FRT")

            self.assertTrue(success)
            self.assertEqual(files_moved, 1)

    # ------------------------------------------------------------------
    # Ошибка бэкапа
    # ------------------------------------------------------------------

    def test_backup_failure_returns_error(self):
        """Если backup_to_old вернул False — success=False, files_moved=0."""
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            (data_dir / "00001.json").write_text("{}", encoding="utf-8")

            with patch("services.file_watcher.deleter.backup_to_old", return_value=False), \
                 patch("services.file_watcher.deleter.invalidate_archive_cache", return_value=0):
                success, files_moved, error_msg = self._run(data_dir, "00001", "1")

            self.assertFalse(success)
            self.assertEqual(files_moved, 0)
            self.assertIn("бэкап", error_msg.lower())

    # ------------------------------------------------------------------
    # Инвалидация кеша вызывается всегда при успехе
    # ------------------------------------------------------------------

    def test_cache_invalidated_when_files_moved(self):
        """invalidate_archive_cache должен вызываться при успешном переносе файлов."""
        with tempfile.TemporaryDirectory() as td:
            data_dir = Path(td)
            (data_dir / "00001.json").write_text("{}", encoding="utf-8")

            with patch("services.file_watcher.deleter.backup_to_old", return_value=True), \
                 patch("services.file_watcher.deleter.invalidate_archive_cache", return_value=1) as mock_cache:
                self._run(data_dir, "00001", "1")

            mock_cache.assert_called_once_with("00001")


if __name__ == "__main__":
    unittest.main()
