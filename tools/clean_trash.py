# Version 1.3 - 22.06.2026 18:24:00 GMT
# Утилита очистки регенерируемого «мусора» проекта.
# Зачем: по триггерам «покажи мусор» / «очисти мусор» безопасно найти и удалить
#        кеши Python (__pycache__/, .pytest_cache/) по всему дереву, файлы в logs/
#        и артефакты прогонов E2E-тестов (tests/playwright-report/, tests/test-results/).
# 1.3: добавлена очистка артефактов Playwright (playwright-report, test-results).
# 1.2: logs/ не удаляется — очищаются только файлы внутри каталога.
# 1.1: onerror для shutil.rmtree — снимает read-only на Windows (Google Drive и т.п.).
"""
clean_trash.py — Поиск и удаление регенерируемых временных артефактов проекта.

Считает «мусором» и обрабатывает:
  - __pycache__/              — кеши байткода Python (по всему дереву), каталог удаляется
  - .pytest_cache/            — кеш pytest (по всему дереву), каталог удаляется
  - logs/*                    — файлы логов в logs/ (каталог logs/ сохраняется)
  - tests/playwright-report/  — HTML-отчёт Playwright, пересоздаётся при каждом прогоне
  - tests/test-results/       — артефакты падений E2E (trace/screenshot/video), пересоздаётся

НЕ трогает и не спускается в данные/окружение:
  data/, data.db/, data.cache/, data.secret/, data.old/, data.up/, data.new/,
  .git/, .venv/, venv/, node_modules/.

Использование (из любого каталога):
    python tools/clean_trash.py            # ПОКАЗАТЬ список мусора (без удаления)
    python tools/clean_trash.py --apply    # УДАЛИТЬ найденный мусор

Показ и удаление используют один и тот же поиск (find_trash), поэтому удаляется
ровно то, что было показано.

Примечание (Windows): при проблемах с кириллицей в консоли:
    $env:PYTHONIOENCODING='utf-8'; python tools/clean_trash.py
"""

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path

# Корень проекта — родитель каталога tools/. Утилита работает только внутри него.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Принудительно UTF-8 на stdout/stderr (важно для Windows-терминалов с cp1252).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PREFIX = "[clean-trash]"

# Кеши, удаляемые везде по дереву.
_TRASH_DIR_NAMES = ("__pycache__", ".pytest_cache")

# Каталоги, в которые НЕ спускаемся: данные, секреты, окружение, история, логи.
# logs/ обрабатывается отдельно — очищаются только файлы внутри.
_SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "logs",
    "data", "data.db", "data.cache", "data.secret",
    "data.old", "data.up", "data.new",
}

# Артефакты прогонов тестов (пути относительно корня проекта).
# Пересоздаются при повторном запуске E2E (Playwright: html-отчёт + test-results),
# поэтому удаляются целиком.
_TEST_ARTIFACT_PATHS = (
    "tests/playwright-report",
    "tests/test-results",
)


def find_trash_dirs(root: Path) -> list[Path]:
    """Собирает каталоги-кеши для полного удаления."""
    targets: list[Path] = []

    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in list(dirnames):
            if name in _TRASH_DIR_NAMES:
                targets.append(Path(dirpath) / name)
                dirnames.remove(name)  # внутрь удаляемого каталога не спускаемся

    return sorted(targets)


def find_log_files(root: Path) -> list[Path]:
    """Собирает файлы логов в logs/ (сам каталог не трогаем)."""
    logs = root / "logs"
    if not logs.is_dir():
        return []
    return sorted(p for p in logs.iterdir() if p.is_file())


def find_test_artifacts(root: Path) -> list[Path]:
    """Существующие каталоги-артефакты прогонов тестов по фиксированным путям."""
    return sorted(
        p for p in (root / rel for rel in _TEST_ARTIFACT_PATHS) if p.exists()
    )


def find_trash(root: Path) -> list[Path]:
    """Единый список мусора: каталоги-кеши, файлы в logs/ и артефакты тестов."""
    return find_trash_dirs(root) + find_log_files(root) + find_test_artifacts(root)


def _remove_readonly_and_retry(func, path: str, _exc_info) -> None:
    """Снимает read-only и повторяет удаление (типичная проблема на Windows/Google Drive)."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def remove_trash_dir(path: Path) -> None:
    """Удаляет каталог мусора; на Windows пробует обойти read-only."""
    shutil.rmtree(path, onerror=_remove_readonly_and_retry)


def remove_trash_file(path: Path) -> None:
    """Удаляет файл; на Windows пробует обойти read-only."""
    try:
        path.unlink()
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Показывает (по умолчанию) или удаляет (--apply) мусор проекта: "
                    "__pycache__/, .pytest_cache/, файлы в logs/.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Удалить найденный мусор. Без флага — только показать список.",
    )
    args = parser.parse_args()

    targets = find_trash(_PROJECT_ROOT)
    if not targets:
        print(f"{PREFIX} Мусор не найден — дерево проекта уже чистое.")
        return 0

    # Режим показа: перечисляем, что считаем мусором, и выходим без изменений.
    if not args.apply:
        print(f"{PREFIX} Найдено к удалению ({len(targets)}):")
        for path in targets:
            print(f"  {path.relative_to(_PROJECT_ROOT)}")
        print(f"{PREFIX} Для удаления: python tools/clean_trash.py --apply")
        return 0

    # Режим удаления.
    removed_dirs = 0
    removed_files = 0
    for path in targets:
        # Защита: удаляем только внутри дерева проекта (отсекаем симлинки наружу).
        if not path.resolve().is_relative_to(_PROJECT_ROOT):
            print(f"{PREFIX} ПРОПУСК (вне проекта): {path}", file=sys.stderr)
            continue

        rel = path.relative_to(_PROJECT_ROOT)
        try:
            if path.is_dir():
                remove_trash_dir(path)
                print(f"{PREFIX} удалён каталог: {rel}")
                removed_dirs += 1
            else:
                remove_trash_file(path)
                print(f"{PREFIX} удалён файл: {rel}")
                removed_files += 1
        except OSError as e:
            print(f"{PREFIX} ОШИБКА при удалении {rel}: {e}", file=sys.stderr)

    parts = []
    if removed_dirs:
        parts.append(f"{removed_dirs} каталог(ов)")
    if removed_files:
        parts.append(f"{removed_files} файл(ов) логов")
    summary = ", ".join(parts) if parts else "0 элементов"
    print(f"{PREFIX} Готово: удалено {summary} из {len(targets)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
