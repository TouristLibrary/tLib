# Version 1.1 - 15.06.2026 10:18:00 GMT
# Диагностика расхождения между data/ и tlib.db.
# 1.1: normalize_stem и make_db_id -> делегаты services.id_utils (устранён дубль нормализации ID).
"""
diagnose_archive_mismatch.py — Диагностика расхождения между файлами в data/ и tlib.db.

Сравнивает физические файлы (.json, .zip, .pdf) в data/ с содержимым таблицы tlib
в БД и выдаёт структурированный отчёт по четырём категориям:

  A. Архив (.zip/.pdf) на диске, JSON отсутствует (архив-сирота)
  B. JSON + архив есть на диске, но РазмерАрхива = 0 или NULL в БД
     (главная причина расхождения db_with_archive_count vs fs_archive_count)
  C. В БД РазмерАрхива > 0, но физического архива нет
  D. JSON есть на диске, строки в БД нет (не должно быть после reindex)

Ключ сопоставления: нормализованный ID = {Шифр_5digits} или {Шифр_5digits}-{ДопШифр_UPPER}.

Использование:
    python tools/diagnose_archive_mismatch.py             # краткий отчёт (первые 20 строк)
    python tools/diagnose_archive_mismatch.py --show-lists  # полные списки

Запускать из корня проекта.
"""

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from services.id_utils import make_norm_id, normalize_group_id  # noqa: E402

PREFIX = "[DIAG]"
SAMPLE_LIMIT = 20


def normalize_stem(stem: str) -> str:
    """Нормализует stem файла. Делегирует в services.id_utils.normalize_group_id."""
    return normalize_group_id(stem)


def make_db_id(shifr, dopshifr) -> str:
    """Собирает нормализованный ID из Шифр/ДопШифр из БД. Делегирует в make_norm_id."""
    return make_norm_id(shifr, dopshifr or "")


def print_section(label: str, items: list, show_all: bool) -> None:
    limit = len(items) if show_all else SAMPLE_LIMIT
    shown = items[:limit]
    for row in shown:
        print(f"{PREFIX}    {row}")
    if not show_all and len(items) > SAMPLE_LIMIT:
        print(f"{PREFIX}    ... и ещё {len(items) - SAMPLE_LIMIT} (используйте --show-lists для полного вывода)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Диагностирует расхождение между файлами в data/ и tlib.db."
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Путь к директории data/ (по умолчанию: data/).",
    )
    parser.add_argument(
        "--db-path",
        default="data.db/tlib.db",
        help="Путь к SQLite БД (по умолчанию: data.db/tlib.db).",
    )
    parser.add_argument(
        "--show-lists",
        action="store_true",
        default=False,
        help="Вывести полные списки вместо первых 20 строк.",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    db_path = Path(args.db_path)

    if not data_dir.exists():
        print(f"{PREFIX} [ERROR] Директория не найдена: {data_dir.resolve()}", file=sys.stderr)
        sys.exit(1)
    if not db_path.exists():
        print(f"{PREFIX} [ERROR] БД не найдена: {db_path.resolve()}", file=sys.stderr)
        sys.exit(1)

    # --- Скан файловой системы ---
    json_stems: dict[str, str] = {}      # normalized_id -> original filename
    archive_stems: dict[str, list[str]] = defaultdict(list)  # normalized_id -> [filenames]

    for f in sorted(data_dir.iterdir()):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        norm = normalize_stem(f.stem)
        if ext == ".json":
            json_stems[norm] = f.name
        elif ext in (".zip", ".pdf"):
            archive_stems[norm].append(f.name)

    fs_json_count = len(json_stems)
    fs_archive_count = sum(len(v) for v in archive_stems.values())

    # --- Загрузка БД ---
    # db_rows: normalized_id -> {РазмерАрхива, ТипФайла, Шифр, ДопШифр}
    db_rows: dict[str, dict] = {}
    db_total = 0
    db_with_archive = 0

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(
            "SELECT Шифр, ДопШифр, РазмерАрхива, ТипФайла FROM tlib"
        )
        for row in cur:
            db_total += 1
            size = row["РазмерАрхива"] or 0
            if size > 0:
                db_with_archive += 1
            norm = make_db_id(row["Шифр"], row["ДопШифр"])
            db_rows[norm] = {
                "РазмерАрхива": size,
                "ТипФайла": (row["ТипФайла"] or "").strip(),
                "Шифр": row["Шифр"],
                "ДопШифр": row["ДопШифр"],
            }
    finally:
        conn.close()

    # --- Вычисление категорий ---

    # A: архив на диске, JSON нет
    cat_a: list[str] = []
    for norm, files in sorted(archive_stems.items()):
        if norm not in json_stems:
            for fname in files:
                cat_a.append(f"{norm:20s}  файл: {fname}")

    # B: JSON + архив на диске, РазмерАрхива = 0/NULL в БД
    cat_b: list[str] = []
    cat_b_by_tipfayla: dict[str, int] = defaultdict(int)
    for norm in sorted(archive_stems.keys()):
        if norm not in json_stems:
            continue  # уже в категории A
        db = db_rows.get(norm)
        if db is None:
            continue  # попадёт в D через json_stems
        if db["РазмерАрхива"] == 0:
            tip = repr(db["ТипФайла"])
            cat_b_by_tipfayla[tip] += 1
            archive_files = ", ".join(archive_stems[norm])
            cat_b.append(
                f"{norm:20s}  ТипФайла={tip:8s}  РазмерАрхива=0  архив: {archive_files}"
            )

    # C: в БД РазмерАрхива > 0, физического архива нет
    cat_c: list[str] = []
    for norm, db in sorted(db_rows.items()):
        if db["РазмерАрхива"] > 0 and norm not in archive_stems:
            tip = repr(db["ТипФайла"])
            cat_c.append(
                f"{norm:20s}  ТипФайла={tip:8s}  РазмерАрхива={db['РазмерАрхива']}"
            )

    # D: JSON на диске, строки в БД нет
    cat_d: list[str] = []
    for norm, fname in sorted(json_stems.items()):
        if norm not in db_rows:
            cat_d.append(f"{norm:20s}  файл: {fname}")

    # --- Отчёт ---
    print(f"{PREFIX} Файловая система ({data_dir.resolve()}):")
    print(f"{PREFIX}   JSON-файлов:           {fs_json_count}")
    print(f"{PREFIX}   Архивов (.zip/.pdf):   {fs_archive_count}")
    print(f"{PREFIX} База данных ({db_path.resolve()}):")
    print(f"{PREFIX}   Всего строк:           {db_total}")
    print(f"{PREFIX}   С РазмерАрхива > 0:   {db_with_archive}")
    print(f"{PREFIX}   Расхождение:           {fs_archive_count - db_with_archive:+d}")
    print()

    print(f"{PREFIX} A. Архив на диске, нет JSON (архив-сирота): {len(cat_a)}")
    if cat_a:
        print_section("A", cat_a, args.show_lists)
    print()

    print(f"{PREFIX} B. JSON + архив на диске, РазмерАрхива=0/NULL в БД: {len(cat_b)}")
    if cat_b:
        print(f"{PREFIX}    По ТипФайла в БД:")
        for tip, cnt in sorted(cat_b_by_tipfayla.items()):
            print(f"{PREFIX}      {tip}: {cnt}")
        print(f"{PREFIX}    {'Все записи:' if args.show_lists else f'Первые {min(SAMPLE_LIMIT, len(cat_b))}:'}")
        print_section("B", cat_b, args.show_lists)
    print()

    print(f"{PREFIX} C. В БД РазмерАрхива>0, нет архива на диске: {len(cat_c)}")
    if cat_c:
        print_section("C", cat_c, args.show_lists)
    print()

    print(f"{PREFIX} D. JSON на диске, нет строки в БД: {len(cat_d)}")
    if cat_d:
        print_section("D", cat_d, args.show_lists)
    print()

    total_issues = len(cat_a) + len(cat_b) + len(cat_c) + len(cat_d)
    if total_issues == 0:
        print(f"{PREFIX} Расхождений не обнаружено.")
    else:
        print(f"{PREFIX} Итого проблемных записей: {total_issues} (A={len(cat_a)}, B={len(cat_b)}, C={len(cat_c)}, D={len(cat_d)})")


if __name__ == "__main__":
    main()
