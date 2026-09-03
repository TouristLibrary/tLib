# Version 1.0 - 12.06.2026
# Вспомогательные функции для integration-тестов TlibWebApp
# Описание: Чистые функции-хелперы без зависимости от pytest-фикстур.
#           Импортируются напрямую в тест-файлах.

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


def sha256hex(s: str) -> str:
    """SHA-256 hex-дайджест строки — дублирует _hash() из auth_db без импорта приватной функции."""
    return hashlib.sha256(s.encode()).hexdigest()


def insert_magic_link_direct(db_path: str, email: str, code: str, *, expired: bool = False) -> str:
    """Вставляет magic link напрямую в auth.db (без HTTP). Возвращает raw token."""
    from datetime import datetime, timezone, timedelta

    token = "test_token_" + email.replace("@", "_").replace(".", "_")
    ttl = -60 if expired else 900
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
    created_at = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO magic_links"
        " (token_hash, code_hash, email, expires_at, created_at, attempts)"
        " VALUES (?, ?, ?, ?, ?, 0)",
        (sha256hex(token), sha256hex(code), email, expires_at, created_at),
    )
    conn.commit()
    conn.close()
    return token


def set_zagruzil_id(tlib_db_path: str, shifr: int, dop: str, user_id: int | None) -> None:
    """Обновляет ЗагрузилID для тестовой записи в tlib.db."""
    from config import DATABASE_TABLE_NAME

    conn = sqlite3.connect(tlib_db_path)
    conn.execute(
        f"UPDATE {DATABASE_TABLE_NAME} SET ЗагрузилID=? WHERE Шифр=? AND ДопШифр=?",
        (user_id, shifr, dop),
    )
    conn.commit()
    conn.close()


def submit_report(
    client,
    shifr: int = 200,
    dopshifr: str = "TEST",
    marshrut: str = "Тестовый маршрут",
    god: int = 2024,
    content: bytes = b"PK fake zip content",
    ext: str = "zip",
):
    """POST /api/upload/submit с минимальными данными. Требует авторизованного client."""
    return client.post(
        "/api/upload/submit",
        data={
            "shifr": shifr,
            "dopshifr": dopshifr,
            "marshrut": marshrut,
            "god": god,
        },
        files={"file": (f"{shifr}-{dopshifr}.{ext}", content, f"application/{ext}")},
    )
