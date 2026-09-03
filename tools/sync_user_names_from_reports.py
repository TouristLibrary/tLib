#!/usr/bin/env python3
# Version 1.0 - 22.06.2026 11:00:00 GMT
# Утилита синхронизации users.name в auth.db из поля ЗагрузилИмя последнего отчёта tlib.db.
"""
Синхронизация users.name в auth.db из ЗагрузилИмя последнего опубликованного отчёта.

Для каждого пользователя из auth.db находит в tlib.db последний отчёт по
ЗагрузилID (ORDER BY ДатаВремяЗагрузки DESC) и, если ЗагрузилИмя не пустое,
записывает его в поле name копии БД auth_new.db. Исходный auth.db не изменяется.

Использование (из корня проекта):
    python tools/sync_user_names_from_reports.py data.db/tlib.db data.secret/auth.db

Опции:
    --output PATH   путь к выходной БД (по умолчанию: <dir auth.db>/auth_new.db)
    --dry-run       показать план изменений без записи

Пример замены prod:
    copy /Y data.secret\\auth_new.db data.secret\\auth.db

Примечание (Windows, кириллица в консоли):
    $env:PYTHONIOENCODING='utf-8'; python tools/sync_user_names_from_reports.py data.db/tlib.db data.secret/auth.db
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

PREFIX = "[SYNC_NAME]"
TLIB_TABLE = "tlib"

LATEST_REPORT_SQL = f"""
SELECT "ЗагрузилID", "ЗагрузилИмя"
FROM (
  SELECT "ЗагрузилID", "ЗагрузилИмя",
         ROW_NUMBER() OVER (
           PARTITION BY "ЗагрузилID"
           ORDER BY "ДатаВремяЗагрузки" DESC, rowid DESC
         ) AS rn
  FROM {TLIB_TABLE}
  WHERE "ЗагрузилID" IS NOT NULL
)
WHERE rn = 1
"""


def _die(msg: str) -> None:
    print(f"{PREFIX} [ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def _check_table(conn: sqlite3.Connection, table: str, db_label: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not row:
        _die(f"таблица {table!r} не найдена в {db_label}")


def load_latest_names(tlib_path: Path) -> dict[int, str]:
    """Возвращает {user_id: ЗагрузилИмя} для последнего отчёта каждого загрузившего."""
    conn = sqlite3.connect(tlib_path)
    try:
        _check_table(conn, TLIB_TABLE, str(tlib_path))
        rows = conn.execute(LATEST_REPORT_SQL).fetchall()
    finally:
        conn.close()

    result: dict[int, str] = {}
    for zagruzil_id, zagruzil_imya in rows:
        if zagruzil_id is None:
            continue
        name = (zagruzil_imya or "").strip()
        if not name:
            continue
        result[int(zagruzil_id)] = name
    return result


def load_auth_user_ids(auth_path: Path) -> list[int]:
    conn = sqlite3.connect(auth_path)
    try:
        _check_table(conn, "users", str(auth_path))
        rows = conn.execute("SELECT id FROM users ORDER BY id").fetchall()
    finally:
        conn.close()
    return [int(r[0]) for r in rows]


def apply_updates(
    auth_path: Path,
    latest_names: dict[int, str],
    user_ids: list[int],
    *,
    dry_run: bool,
) -> tuple[int, int]:
    """
    Обновляет users.name в auth_path.
    Возвращает (updated, skipped) — skipped = нет отчёта или пустое ЗагрузилИмя.
    """
    updated = 0
    skipped = sum(1 for uid in user_ids if uid not in latest_names)

    conn = sqlite3.connect(auth_path)
    try:
        for uid in user_ids:
            if uid not in latest_names:
                continue

            new_name = latest_names[uid]
            row = conn.execute(
                "SELECT name FROM users WHERE id = ?", (uid,)
            ).fetchone()
            if row is None:
                continue

            old_name = row[0] or ""
            if dry_run:
                print(f"{PREFIX}   id={uid}: {old_name!r} -> {new_name!r}")
            else:
                conn.execute(
                    "UPDATE users SET name = ? WHERE id = ?",
                    (new_name, uid),
                )
            updated += 1

        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    return updated, skipped


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy auth.db to auth_new.db; set users.name from latest tlib report."
    )
    parser.add_argument(
        "tlib_db",
        type=Path,
        help="Path to tlib.db (e.g. data.db/tlib.db).",
    )
    parser.add_argument(
        "auth_db",
        type=Path,
        help="Path to auth.db (e.g. data.secret/auth.db).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output DB path (default: <auth_dir>/auth_new.db).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned changes without writing output DB.",
    )
    args = parser.parse_args()

    tlib_path = args.tlib_db.resolve()
    auth_path = args.auth_db.resolve()
    output_path = (
        args.output.resolve()
        if args.output is not None
        else auth_path.parent / "auth_new.db"
    )

    if not tlib_path.is_file():
        _die(f"файл не найден: {tlib_path}")
    if not auth_path.is_file():
        _die(f"файл не найден: {auth_path}")

    print(f"{PREFIX} tlib:   {tlib_path}")
    print(f"{PREFIX} auth:   {auth_path}")
    print(f"{PREFIX} output: {output_path}")
    if args.dry_run:
        print(f"{PREFIX} режим: dry-run (без записи)")

    latest_names = load_latest_names(tlib_path)
    print(f"{PREFIX} загрузивших с непустым ЗагрузилИмя в последнем отчёте: {len(latest_names)}")

    user_ids = load_auth_user_ids(auth_path)
    print(f"{PREFIX} пользователей в auth: {len(user_ids)}")

    work_path = auth_path
    if not args.dry_run:
        shutil.copy2(auth_path, output_path)
        work_path = output_path

    updated, skipped = apply_updates(
        work_path,
        latest_names,
        user_ids,
        dry_run=args.dry_run,
    )

    print(f"{PREFIX} --- итог ---")
    print(f"{PREFIX}   обновлено name:              {updated}")
    print(f"{PREFIX}   без отчёта / пустое имя:    {skipped}")
    if not args.dry_run:
        print(f"{PREFIX}   записано в:                 {output_path}")
    else:
        print(f"{PREFIX}   (dry-run: файл не создан)")


if __name__ == "__main__":
    main()
