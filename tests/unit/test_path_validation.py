# Version 1.1 - 21.06.2026 19:00:00 GMT
# Path validation tests for TlibWebApp
# Описание: Набор unit/regression тестов для единого валидатора путей.
#           Проверяет, что безопасные имена файлов и zip-member пути проходят,
#           а попытки Path Traversal, абсолютные пути, backslash, Windows drive/UNC и double-encoding
#           корректно отклоняются контролируемой ошибкой (400/403), без падений.
# 1.1: добавлен кейс nested-путь с require_basename=False (cache/png-router сценарий).

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from services.security.path_validation import (
    PathValidationError,
    decode_url_path,
    validate_and_resolve_under_base,
    validate_zip_member_path,
)


class TestDecodeUrlPath(unittest.TestCase):
    def test_decode_url_path_stops_when_stable(self):
        self.assertEqual(decode_url_path("abc%20def"), "abc def")
        self.assertEqual(decode_url_path("plain"), "plain")

    def test_decode_url_path_double_encoded(self):
        raw = "%252e%252e%252fetc%252fpasswd"
        self.assertEqual(decode_url_path(raw), "../etc/passwd")


class TestValidateAndResolveUnderBase(unittest.TestCase):
    def test_ok_basename_pdf_allows_double_dots(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "document..pdf").write_bytes(b"%PDF-1.4")
            p = validate_and_resolve_under_base(
                base,
                "document..pdf",
                require_basename=True,
                allowed_suffixes=[".pdf"],
            )
            self.assertTrue(p.is_file())

    def test_ok_basename_pdf_allows_spaces_when_encoded(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "name with space.pdf").write_bytes(b"%PDF-1.4")
            p = validate_and_resolve_under_base(
                base,
                "name%20with%20space.pdf",
                require_basename=True,
                allowed_suffixes=[".pdf"],
            )
            self.assertEqual(p.name, "name with space.pdf")

    def test_reject_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with self.assertRaises(PathValidationError) as ctx:
                validate_and_resolve_under_base(base, "../etc/passwd")
            self.assertEqual(ctx.exception.status_code, 400)

    def test_reject_double_encoded_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            raw = "%252e%252e%252fetc%252fpasswd"
            with self.assertRaises(PathValidationError) as ctx:
                validate_and_resolve_under_base(base, raw)
            self.assertEqual(ctx.exception.status_code, 400)

    def test_reject_absolute_posix(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with self.assertRaises(PathValidationError) as ctx:
                validate_and_resolve_under_base(base, "/etc/passwd")
            self.assertEqual(ctx.exception.status_code, 400)

    def test_reject_backslash(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with self.assertRaises(PathValidationError) as ctx:
                validate_and_resolve_under_base(base, r"..\x")
            self.assertEqual(ctx.exception.status_code, 400)

    def test_reject_windows_drive(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with self.assertRaises(PathValidationError) as ctx:
                validate_and_resolve_under_base(base, "C:/Windows/System32/drivers/etc/hosts")
            self.assertEqual(ctx.exception.status_code, 400)

    def test_reject_unc(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with self.assertRaises(PathValidationError) as ctx:
                validate_and_resolve_under_base(base, r"\\server\share\file.txt")
            self.assertEqual(ctx.exception.status_code, 400)

    def test_ok_nested_path_require_basename_false(self):
        """Вложенный путь без traversal проходит при require_basename=False (PNG/cache сценарий)."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            nested = base / "subdir" / "report-png"
            nested.mkdir(parents=True)
            p = validate_and_resolve_under_base(
                base,
                "subdir/report-png",
                require_basename=False,
            )
            self.assertEqual(p.resolve(), nested.resolve())

    def test_reject_traversal_nested_require_basename_false(self):
        """Traversal в среднем сегменте вложенного пути отвергается."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            with self.assertRaises(PathValidationError) as ctx:
                validate_and_resolve_under_base(
                    base,
                    "subdir/../../etc",
                    require_basename=False,
                )
            self.assertEqual(ctx.exception.status_code, 400)

    def test_symlink_escape_is_403_when_supported(self):
        # На Windows без прав администратора symlink может быть недоступен — в этом случае тест пропускаем.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "base"
            outside = Path(td) / "outside"
            base.mkdir(parents=True, exist_ok=True)
            outside.mkdir(parents=True, exist_ok=True)
            (outside / "secret.txt").write_text("secret", encoding="utf-8")

            link = base / "link"
            try:
                os.symlink(str(outside), str(link), target_is_directory=True)
            except Exception:
                self.skipTest("Symlink creation not supported in this environment")

            with self.assertRaises(PathValidationError) as ctx:
                validate_and_resolve_under_base(base, "link/secret.txt")
            self.assertEqual(ctx.exception.status_code, 403)


class TestValidateZipMemberPath(unittest.TestCase):
    def test_ok_zip_member_path(self):
        p = validate_zip_member_path("1-TST/%D0%A4%D0%BE%D1%82%D0%BE%201.jpg")
        self.assertEqual(p, "1-TST/Фото 1.jpg")

    def test_reject_zip_member_traversal(self):
        with self.assertRaises(PathValidationError) as ctx:
            validate_zip_member_path("../x")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_reject_zip_member_absolute(self):
        with self.assertRaises(PathValidationError) as ctx:
            validate_zip_member_path("/etc/passwd")
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()

