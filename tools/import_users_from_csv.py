#!/usr/bin/env python3
# Version 1.2 - 22.06.2026 11:00:00 GMT
# Утилита импорта пользователей из CSV в auth.db (SQLite).
# Изменения v1.1: схема magic_links синхронизирована с init_auth_db() (code_hash, attempts).
# Изменения v1.2: AUTH_SCHEMA_SQL импортируется из services.auth.auth_db (единый источник истины).
"""
Импорт пользователей из CSV-файла в новую auth.db (SQLite).

Использование:
    python tools/import_users_from_csv.py <путь/к/файлу.csv>

Пример:
    python tools/import_users_from_csv.py data.secret/tblUser.csv

Результат (в той же директории, что и CSV):
    <stem>.db   — SQLite с таблицами users, magic_links, sessions, app_settings
    <stem>.err  — отчёт по пропущенным строкам (разделы по типу ошибки)

После импорта скопируйте .db в data.secret/auth.db:
    Windows:  copy /Y data.secret\\tblUser.db data.secret\\auth.db
    Linux:    cp data.secret/tblUser.db data.secret/auth.db

Затем выдайте роль admin при необходимости:
    python tools/manage_users.py grant admin@example.com admin

Примечание (Windows, кириллица в консоли):
    $env:PYTHONIOENCODING='utf-8'; python tools/import_users_from_csv.py data.secret/tblUser.csv
"""

import argparse
import csv
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Корень проекта в sys.path для импорта из services/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(_PROJECT_ROOT / "data.secret/.env")

from services.auth.auth_db import AUTH_SCHEMA_SQL  # noqa: E402

# ---------------------------------------------------------------------------
# Типы данных
# ---------------------------------------------------------------------------

@dataclass
class ErrRow:
    line_no: int
    raw_id: str
    login: str
    email: str
    reason: str
    extra: dict = field(default_factory=dict)


@dataclass
class Candidate:
    """Валидная строка CSV, претендующая на запись в БД."""
    line_no: int
    raw_id: str
    user_id: int
    login: str
    email: str       # уже lower()
    selected: bool = False  # True у победителя (max id) в email-группе


@dataclass
class Stats:
    imported: int = 0
    skipped_duplicate: int = 0
    skipped_invalid: int = 0
    skipped_id_conflict: int = 0
    max_id: int = 0


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def derive_output_paths(csv_path: Path) -> tuple[Path, Path]:
    """Возвращает (db_path, err_path) рядом с csv_path."""
    stem = csv_path.stem
    parent = csv_path.parent
    return parent / f"{stem}.db", parent / f"{stem}.err"


# ---------------------------------------------------------------------------
# Парсинг CSV
# ---------------------------------------------------------------------------

def parse_rows(csv_path: Path) -> list[dict]:
    """Читает CSV, возвращает список строк с нормализованными ключами."""
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        # Нормализуем заголовки: strip пробелов
        reader.fieldnames = [k.strip() for k in (reader.fieldnames or [])]
        return [
            {k.strip(): (v or "").strip() for k, v in row.items()}
            for row in reader
        ]


# ---------------------------------------------------------------------------
# Основной импорт
# ---------------------------------------------------------------------------

def import_users(
    rows: list[dict],
    db_path: Path,
) -> tuple[Stats, dict[str, list[Candidate]], list[ErrRow], list[ErrRow]]:
    """
    Создаёт db_path, импортирует строки.
    Возвращает (stats, dup_groups, invalids, conflicts).

    dup_groups: dict[email -> list[Candidate]] только для email с дублями.
    Победитель в каждой группе выбирается по максимальному ID (tie-break: max line_no)
    и помечается Candidate.selected = True.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(AUTH_SCHEMA_SQL)

    stats = Stats()
    invalids: list[ErrRow] = []
    conflicts: list[ErrRow] = []

    # --- Проход 1: валидация и группировка по email ---
    email_groups: dict[str, list[Candidate]] = {}  # порядок первого появления email

    for line_idx, row in enumerate(rows, start=2):  # строка 1 = заголовок
        raw_id = row.get("ID", "")
        login = row.get("Login", "")
        raw_email = row.get("email", "")

        # Пустая строка
        if not raw_id and not login and not raw_email:
            invalids.append(ErrRow(line_idx, raw_id, login, raw_email, "empty_row"))
            stats.skipped_invalid += 1
            continue

        # Валидация email
        email = raw_email.lower()
        if "@" not in email:
            invalids.append(ErrRow(line_idx, raw_id, login, raw_email, "invalid_email"))
            stats.skipped_invalid += 1
            continue

        # Валидация ID
        if not raw_id.strip().lstrip("-").isdigit():
            invalids.append(ErrRow(line_idx, raw_id, login, raw_email, "invalid_id"))
            stats.skipped_invalid += 1
            continue
        user_id = int(raw_id.strip())

        if email not in email_groups:
            email_groups[email] = []
        email_groups[email].append(Candidate(line_idx, raw_id, user_id, login, email))

    # --- Проход 2: выбор победителя (min id, tie-break: min line_no) ---
    dup_groups: dict[str, list[Candidate]] = {}
    winners: list[Candidate] = []

    for email, group in email_groups.items():
        winner = min(group, key=lambda c: (c.user_id, c.line_no))
        winner.selected = True
        winners.append(winner)
        if len(group) > 1:
            dup_groups[email] = group
            stats.skipped_duplicate += len(group) - 1

    # --- Проход 3: вставка победителей в БД, проверка id_conflict ---
    seen_id: dict[int, str] = {}  # id → email
    now = _now_iso()

    for cand in winners:
        if cand.user_id in seen_id:
            conflict_email = seen_id[cand.user_id]
            conflicts.append(ErrRow(
                cand.line_no, cand.raw_id, cand.login, cand.email, "id_conflict",
                extra={"conflict_id": str(cand.user_id), "conflict_email": conflict_email},
            ))
            stats.skipped_id_conflict += 1
            continue

        name = cand.login or cand.email.split("@")[0]
        conn.execute(
            "INSERT INTO users (id, email, name, role, is_active, created_at) VALUES (?, ?, ?, '', 1, ?)",
            (cand.user_id, cand.email, name, now),
        )
        seen_id[cand.user_id] = cand.email
        stats.imported += 1
        if cand.user_id > stats.max_id:
            stats.max_id = cand.user_id

    # Обновляем sqlite_sequence, чтобы следующий AUTOINCREMENT не конфликтовал
    if stats.imported > 0:
        conn.execute(
            "INSERT OR REPLACE INTO sqlite_sequence (name, seq) VALUES ('users', (SELECT MAX(id) FROM users))"
        )

    conn.commit()
    conn.close()
    return stats, dup_groups, invalids, conflicts


# ---------------------------------------------------------------------------
# Файл .err
# ---------------------------------------------------------------------------

def write_err_report(
    err_path: Path,
    dup_groups: dict[str, list[Candidate]],
    invalids: list[ErrRow],
    conflicts: list[ErrRow],
) -> None:
    lines: list[str] = []

    # --- duplicate_email ---
    # Блоки по email; строки: "email login ID" (выбранный ID помечен *)
    lines.append("# duplicate_email")
    if dup_groups:
        first_block = True
        for email, group in dup_groups.items():
            if not first_block:
                lines.append("")
            first_block = False
            for cand in group:
                id_str = f"*{cand.user_id}" if cand.selected else str(cand.user_id)
                lines.append(f"{email} {cand.login} {id_str}")
    else:
        lines.append("count: 0")

    lines.append("")

    # --- invalid ---
    lines.append("# invalid")
    lines.append("# columns: line_no;ID;Login;email;reason")
    if invalids:
        for r in invalids:
            lines.append(f"{r.line_no};{r.raw_id};{r.login};{r.email};{r.reason}")
    else:
        lines.append("count: 0")

    lines.append("")

    # --- id_conflict ---
    lines.append("# id_conflict")
    lines.append("# columns: line_no;ID;Login;email;reason;conflict_id;conflict_email")
    if conflicts:
        for r in conflicts:
            lines.append(
                f"{r.line_no};{r.raw_id};{r.login};{r.email};{r.reason}"
                f";{r.extra.get('conflict_id', '')};{r.extra.get('conflict_email', '')}"
            )
    else:
        lines.append("count: 0")

    err_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Импорт tblUser.csv → <stem>.db + <stem>.err",
        epilog=(
            "Пример: python tools/import_users_from_csv.py data.secret/tblUser.csv\n"
            "Windows (кириллица): $env:PYTHONIOENCODING='utf-8'"
        ),
    )
    parser.add_argument("csv", metavar="CSV", help="Путь к входному CSV-файлу")
    args = parser.parse_args()

    csv_path = Path(args.csv).resolve()
    if not csv_path.exists():
        print(f"[ERROR] Файл не найден: {csv_path}", file=sys.stderr)
        return 1
    if not csv_path.is_file():
        print(f"[ERROR] Не файл: {csv_path}", file=sys.stderr)
        return 1

    db_path, err_path = derive_output_paths(csv_path)

    # Перезапись: удалить старую БД, чтобы начать с чистого листа
    if db_path.exists():
        db_path.unlink()

    try:
        rows = parse_rows(csv_path)
    except Exception as exc:
        print(f"[ERROR] Не удалось прочитать CSV: {exc}", file=sys.stderr)
        return 1

    stats, dup_groups, invalids, conflicts = import_users(rows, db_path)
    write_err_report(err_path, dup_groups, invalids, conflicts)

    print("[IMPORT] OK")
    print(f"  imported:            {stats.imported}")
    print(f"  skipped_duplicate:   {stats.skipped_duplicate}")
    print(f"  skipped_invalid:     {stats.skipped_invalid}")
    print(f"  skipped_id_conflict: {stats.skipped_id_conflict}")
    print(f"  max_id:              {stats.max_id}")
    print(f"  database:            {db_path}")
    print(f"  errors:              {err_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
