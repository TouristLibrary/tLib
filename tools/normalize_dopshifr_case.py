#!/usr/bin/env python3
# Version 1.0 - 26.07.2026 08:40:00 GMT
# Утилита приведения ДопШифр к верхнему регистру в data/.
"""
normalize_dopshifr_case.py — Привести ДопШифр к верхнему регистру во всех файлах data/.

Для каждого файла .json/.zip/.pdf в data/:
  - Если ДопШифр в имени файла написан в нижнем регистре — переименовывает файл.
  - Для .json дополнительно исправляет поле "ДопШифр" внутри JSON (без переформатирования)
    и кладёт бэкап оригинала в data.old/ перед записью.

По умолчанию — сухой прогон: изменения только показываются.
Для реального применения передать флаг --apply.

Использование (из корня проекта):
    python3 tools/normalize_dopshifr_case.py
    python3 tools/normalize_dopshifr_case.py --apply
    python3 tools/normalize_dopshifr_case.py --data-dir /mnt/usb/data --apply
    python3 tools/normalize_dopshifr_case.py --data-dir data --backup-dir data.old --apply

Примечание о кэше:
    Папки data.cache/<stem>/ именуются по stem-у файла.  После переименования
    data/00012-frt.zip → 00012-FRT.zip папка data.cache/00012-frt/ осиротеет —
    она пересоздаётся по запросу и вытесняется LRU.  Утилита кэш не трогает.

Порядок применения на сервере:
    1. mkdir -p data.up/pause          # пауза File Watcher
    2. python3 tools/normalize_dopshifr_case.py        # сухой прогон, проверить список
    3. python3 tools/normalize_dopshifr_case.py --apply
    4. rmdir data.up/pause             # снять паузу
    5. touch data.up/20_go/reindex.txt # принудительная переиндексация БД
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from services.file_watcher.scanner import parse_filename  # noqa: E402
from services.id_utils import normalize_dopshifr           # noqa: E402

PREFIX = "[DOPCASE]"
REPORT_EXTENSIONS = {".json", ".zip", ".pdf"}
UTF8_BOM = b"\xef\xbb\xbf"


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _die(msg: str) -> None:
    print(f"{PREFIX} [ERROR] {msg}", file=sys.stderr)
    sys.exit(1)


def _target_name(filename: str) -> str | None:
    """
    Вычисляет целевое имя файла после нормализации ДопШифра.

    Возвращает новое имя если нужна правка, None если имя уже каноническое
    или файл не подпадает под паттерн (нет ДопШифра).
    """
    parsed = parse_filename(filename)
    if not parsed or not parsed.get("dopshifr"):
        return None
    dop_orig = parsed["dopshifr"]
    dop_upper = normalize_dopshifr(dop_orig)
    if dop_orig == dop_upper:
        return None
    # Заменяем только ДопШифр-часть, Шифр берём как есть
    shifr = parsed["shifr"]
    ext = parsed["ext"]
    return f"{shifr}-{dop_upper}{ext}"


class _JsonPatch(NamedTuple):
    new_text: str
    had_bom: bool
    old_value: str
    new_value: str


def _patch_json_text(raw_bytes: bytes, dop_from_filename: str) -> _JsonPatch | None:
    """
    Минимальная правка: заменяет значение поля "ДопШифр" в тексте без переформатирования.

    Возвращает _JsonPatch или None если правка не нужна / не безопасна (вывод
    предупреждения при этом — на стороне вызывающего).

    raises ValueError с человекочитаемым сообщением при небезопасной ситуации.
    """
    had_bom = raw_bytes.startswith(UTF8_BOM)
    text = raw_bytes.decode("utf-8-sig")

    data = json.loads(text)
    dop_json = data.get("ДопШифр")

    # Нет поля, пусто или null
    if not dop_json:
        return None

    dop_json_str = str(dop_json)
    dop_upper = normalize_dopshifr(dop_json_str)

    # Уже в верхнем регистре
    if dop_json_str == dop_upper:
        return None

    # Проверяем соответствие имени файла и содержимого (регистронезависимо)
    if dop_json_str.lower() != dop_from_filename.lower():
        raise ValueError(
            f"ДопШифр в JSON ('{dop_json_str}') не совпадает с ДопШифр в имени файла "
            f"('{dop_from_filename}') — рассогласование данных, файл не трогаем"
        )

    # Текстовая замена ровно одного вхождения
    pattern = r'"ДопШифр"\s*:\s*"' + re.escape(dop_json_str) + r'"'
    replacement = f'"ДопШифр": "{dop_upper}"'
    matches = re.findall(pattern, text)
    if len(matches) != 1:
        raise ValueError(
            f"Паттерн замены ДопШифр найден {len(matches)} раз (ожидался ровно 1) — "
            f"возможно, значение закодировано escape-последовательностями; файл не трогаем"
        )

    new_text = re.sub(pattern, replacement, text, count=1)

    # Верификация: результат должен отличаться ровно одним полем
    new_data = json.loads(new_text)
    expected = dict(data)
    expected["ДопШифр"] = dop_upper
    if new_data != expected:
        raise ValueError(
            "Верификация после замены не прошла — итоговый JSON отличается неожиданно; "
            "файл не трогаем"
        )

    return _JsonPatch(new_text, had_bom, dop_json_str, dop_upper)


# ---------------------------------------------------------------------------
# Основная логика
# ---------------------------------------------------------------------------

def scan_and_process(
    data_dir: Path,
    backup_dir: Path,
    *,
    apply: bool,
) -> dict:
    """
    Обходит data_dir, собирает правки, при apply=True применяет их.

    Возвращает сводку: dict с ключами scanned, renamed_json, renamed_zip,
    renamed_pdf, json_fields_fixed, skipped, conflicts, warnings.
    """
    stats = {
        "scanned": 0,
        "renamed_json": 0,
        "renamed_zip": 0,
        "renamed_pdf": 0,
        "json_fields_fixed": 0,
        "skipped": 0,
        "conflicts": 0,
        "warnings": 0,
    }

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    for file_path in sorted(data_dir.iterdir()):
        if not file_path.is_file():
            continue
        ext_lower = file_path.suffix.lower()
        if ext_lower not in REPORT_EXTENSIONS:
            continue

        stats["scanned"] += 1

        new_name = _target_name(file_path.name)

        # Для JSON — отдельно проверяем и правим содержимое
        json_patch: _JsonPatch | None = None
        dop_from_filename = None

        if ext_lower == ".json":
            parsed = parse_filename(file_path.name)
            dop_from_filename = parsed["dopshifr"] if parsed else None

            if dop_from_filename:
                try:
                    raw = file_path.read_bytes()
                    json_patch = _patch_json_text(raw, dop_from_filename)
                except ValueError as e:
                    print(f"{PREFIX} [WARN] {file_path.name}: {e}")
                    stats["warnings"] += 1
                    # Продолжаем — переименование (если нужно) всё равно пытаемся
                except Exception as e:
                    print(f"{PREFIX} [WARN] {file_path.name}: ошибка чтения JSON: {e}")
                    stats["warnings"] += 1

        # Нет ни правки поля, ни переименования
        if new_name is None and json_patch is None:
            stats["skipped"] += 1
            continue

        # Проверка конфликта имени (целевой файл уже существует)
        if new_name is not None:
            target_path = file_path.parent / new_name
            if target_path.exists() and target_path.resolve() != file_path.resolve():
                print(
                    f"{PREFIX} [CONFLICT] {file_path.name} → {new_name}: "
                    f"целевой файл уже существует, пропускаем"
                )
                stats["conflicts"] += 1
                continue

        # Сухой прогон: только вывод
        if not apply:
            if json_patch is not None:
                print(
                    f"{PREFIX}   {file_path.name}: "
                    f"ДопШифр '{json_patch.old_value}' → '{json_patch.new_value}' (JSON-поле)"
                )
            if new_name is not None:
                print(f"{PREFIX}   {file_path.name} → {new_name} (переименование)")
            if json_patch is not None:
                stats["json_fields_fixed"] += 1
            if new_name is not None:
                _count_rename(stats, ext_lower)
            continue

        # --- Рабочий прогон ---

        # 1. Правка JSON-содержимого + бэкап оригинала
        if json_patch is not None:
            backup_dir.mkdir(parents=True, exist_ok=True)
            stem = file_path.stem
            backup_name = f"{stem}_{timestamp}.json"
            backup_path = backup_dir / backup_name
            shutil.copy2(str(file_path), str(backup_path))
            print(f"{PREFIX}   бэкап: {file_path.name} → data.old/{backup_name}")

            out_bytes = json_patch.new_text.encode("utf-8")
            if json_patch.had_bom:
                out_bytes = UTF8_BOM + out_bytes
            file_path.write_bytes(out_bytes)
            print(
                f"{PREFIX}   {file_path.name}: "
                f"ДопШифр '{json_patch.old_value}' → '{json_patch.new_value}' (JSON-поле)"
            )
            stats["json_fields_fixed"] += 1

        # 2. Переименование файла
        if new_name is not None:
            target_path = file_path.parent / new_name
            file_path.rename(target_path)
            print(f"{PREFIX}   {file_path.name} → {new_name}")
            _count_rename(stats, ext_lower)


    return stats


def _count_rename(stats: dict, ext_lower: str) -> None:
    if ext_lower == ".json":
        stats["renamed_json"] += 1
    elif ext_lower == ".zip":
        stats["renamed_zip"] += 1
    elif ext_lower == ".pdf":
        stats["renamed_pdf"] += 1


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Привести ДопШифр к верхнему регистру в именах файлов и в JSON-полях в data/.\n"
            "По умолчанию — сухой прогон (изменения только показываются)."
        )
    )
    parser.add_argument(
        "--data-dir",
        default="data",
        type=Path,
        help="Путь к директории data/ (по умолчанию: data/).",
    )
    parser.add_argument(
        "--backup-dir",
        default="data.old",
        type=Path,
        help="Директория для бэкапов изменяемых JSON (по умолчанию: data.old/).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Применить изменения (без флага — только сухой прогон).",
    )
    args = parser.parse_args()

    data_dir: Path = args.data_dir.resolve()
    backup_dir: Path = args.backup_dir.resolve()

    if not data_dir.is_dir():
        _die(f"директория не найдена: {data_dir}")

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"{PREFIX} data:   {data_dir}")
    print(f"{PREFIX} backup: {backup_dir}")
    print(f"{PREFIX} режим:  {mode}")
    print()

    stats = scan_and_process(data_dir, backup_dir, apply=args.apply)

    renamed_total = stats["renamed_json"] + stats["renamed_zip"] + stats["renamed_pdf"]
    print()
    print(f"{PREFIX} --- итог ---")
    print(f"{PREFIX}   просмотрено файлов:     {stats['scanned']}")
    print(f"{PREFIX}   переименовано json:      {stats['renamed_json']}")
    print(f"{PREFIX}   переименовано zip:       {stats['renamed_zip']}")
    print(f"{PREFIX}   переименовано pdf:       {stats['renamed_pdf']}")
    print(f"{PREFIX}   итого переименовано:     {renamed_total}")
    print(f"{PREFIX}   исправлено JSON-полей:   {stats['json_fields_fixed']}")
    print(f"{PREFIX}   без изменений:           {stats['skipped']}")
    print(f"{PREFIX}   конфликтов имён:         {stats['conflicts']}")
    print(f"{PREFIX}   предупреждений:          {stats['warnings']}")

    if not args.apply and (renamed_total > 0 or stats["json_fields_fixed"] > 0):
        print()
        print(f"{PREFIX} (dry-run: файлы не изменены. Для применения добавьте --apply)")

    if stats["conflicts"] > 0 or stats["warnings"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
