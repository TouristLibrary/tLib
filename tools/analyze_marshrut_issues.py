# Version 1.1 - 15.06.2026 10:18:00 GMT
# Анализ проблем поля «Маршрут» в tlib.db.
# 1.1: make_id -> делегат services.id_utils.make_norm_id (устранён дубль нормализации ID).
"""
analyze_marshrut_issues.py — Анализ проблем поля «Маршрут» в tlib.db (read-only).

Сканирует все строки таблицы tlib, классифицирует каждое значение поля «Маршрут»
по категориям проблем (зеркалируя правила normalizeRoute из js/upload.js),
выводит список затронутых отчётов и сводную статистику.

Использование:
    python tools/analyze_marshrut_issues.py                         # первые 30 отчётов
    python tools/analyze_marshrut_issues.py --show-lists            # все отчёты
    python tools/analyze_marshrut_issues.py --limit 50              # первые 50
    python tools/analyze_marshrut_issues.py --csv out.csv           # экспорт в CSV
    python tools/analyze_marshrut_issues.py --db-path другой.db

Запускать из корня проекта.
"""

import argparse
import csv
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from services.id_utils import make_norm_id  # noqa: E402

# Принудительно UTF-8 на stdout/stderr (важно для Windows-терминалов с cp1252).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

PREFIX = "[MARSHRUT]"
SAMPLE_LIMIT = 30

# ---------------------------------------------------------------------------
# Порты normalizeText / normalizeRoute из js/upload.js.
# Структура зеркалирует JS: normalizeText — общая часть,
# normalizeRoute — вызывает normalizeText и добавляет обработку «=».
# ---------------------------------------------------------------------------

_RE_INVISIBLE   = re.compile(r"[\u00AD\u200B-\u200F\u2060-\u2064\u2066-\u2069\uFEFF]")
_RE_SMART_DASH  = re.compile(r"[\u2010-\u2015\u2212]")
_RE_SMART_DQUOTE = re.compile(r"[\u201C\u201D\u201E\u201F\u00AB\u00BB]")
_RE_SMART_SQUOTE = re.compile(r"[\u2018\u2019\u201A\u201B\u2032]")
_RE_ELLIPSIS    = re.compile(r"\u2026")
# «Любые» пробелы — явный класс для полного контроля (не полагаемся на \s).
_RE_ANY_SPACE   = re.compile(r"[\t\n\r\f\v \u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000]")
_RE_NON_WL      = re.compile(r"[^\u0020-\u007E\u00B0\u0400-\u04FF\u2116]")
_RE_EQ_MULTI    = re.compile(r"(?:\s*=\s*)+")
_RE_MULTI_SPACE = re.compile(r" {2,}")
_RE_DANGLING_EQ = re.compile(r"^[\s=]+|[\s=]+$")


def _normalize_text(value: str) -> str:
    """Порт normalizeText() из js/upload.js: общая чистка без обработки «=»."""
    if not value:
        return ""
    s = str(value)
    s = unicodedata.normalize("NFC", s)
    s = _RE_INVISIBLE.sub("", s)
    s = _RE_SMART_DASH.sub("-", s)
    s = _RE_SMART_DQUOTE.sub('"', s)
    s = _RE_SMART_SQUOTE.sub("'", s)
    s = _RE_ELLIPSIS.sub("...", s)
    s = _RE_ANY_SPACE.sub(" ", s)
    s = _RE_NON_WL.sub("", s)
    s = _RE_MULTI_SPACE.sub(" ", s)
    return s.strip()


def normalize_route(value: str) -> str:
    """Порт normalizeRoute() из js/upload.js: normalizeText + обработка разделителя «=»."""
    s = _normalize_text(value)
    if not s:
        return ""
    s = _RE_EQ_MULTI.sub(" = ", s)
    s = _RE_MULTI_SPACE.sub(" ", s)
    s = re.sub(r"^\s*=\s*|\s*=\s*$", "", s)
    return s.strip()


# ---------------------------------------------------------------------------
# Детекторы по категориям (каждый: str -> bool)
# ---------------------------------------------------------------------------

_DET_INVISIBLE   = re.compile(r"[\u00AD\u200B-\u200F\u2060-\u2064\u2066-\u2069\uFEFF]")
_DET_CONTROL     = re.compile(r"[\u0000-\u0008\u000E-\u001F\u007F-\u009F]")
_DET_SMART_DASH  = re.compile(r"[\u2010-\u2015\u2212]")
_DET_SMART_QUOTE = re.compile(r"[\u201C\u201D\u201E\u201F\u00AB\u00BB\u2018\u2019\u201A\u201B\u2032]")
_DET_ELLIPSIS    = re.compile(r"\u2026")
_DET_WEIRD_SPACE = re.compile(r"[\t\n\r\f\v\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000]")
_DET_MULTI_SP    = re.compile(r" {2,}")
# EQ_SPACING: разделитель «=» не оформлен как « = » или есть дубли
_DET_EQ_SPACING  = re.compile(r"(?:\s*=\s*)+")
# NON_WHITELIST после всех «умных» замен и invisible — остаток вне белого списка
_DET_NON_WL_FULL = re.compile(
    r"[^\u0020-\u007E\u00B0\u0400-\u04FF\u2116"
    r"\u00AD\u200B-\u200F\u2060-\u2064\u2066-\u2069\uFEFF"
    r"\u0000-\u0008\u000E-\u001F\u007F-\u009F"
    r"\u2010-\u2015\u2212"
    r"\u201C\u201D\u201E\u201F\u00AB\u00BB\u2018\u2019\u201A\u201B\u2032"
    r"\u2026"
    r"\t\n\r\f\v\u00A0\u1680\u2000-\u200A\u2028\u2029\u202F\u205F\u3000"
    r"]"
)


def _eq_spacing_bad(raw: str) -> bool:
    """True, если хотя бы один «=» оформлен не как ' = ' или есть дубли."""
    # Ищем все вхождения «=» с окружающими пробелами; если хотя бы одно
    # не равно ровно ' = ' — проблема.
    for m in _DET_EQ_SPACING.finditer(raw):
        if m.group() != " = ":
            return True
    return False


def detect_issues(raw: str) -> list[str]:
    """
    Возвращает список кодов проблем для одного значения поля «Маршрут».
    Пустая строка → ['EMPTY'].
    """
    issues: list[str] = []

    stripped = raw.strip()
    if not stripped:
        issues.append("EMPTY")
        return issues

    if raw != raw.strip():
        issues.append("EDGE_SPACE")
    if _DET_INVISIBLE.search(raw):
        issues.append("INVISIBLE")
    if _DET_CONTROL.search(raw):
        issues.append("CONTROL")
    if _DET_SMART_DASH.search(raw):
        issues.append("SMART_DASH")
    if _DET_SMART_QUOTE.search(raw):
        issues.append("SMART_QUOTE")
    if _DET_ELLIPSIS.search(raw):
        issues.append("ELLIPSIS")
    if raw != unicodedata.normalize("NFC", raw):
        issues.append("NON_NFC")
    if _DET_WEIRD_SPACE.search(raw):
        issues.append("WEIRD_SPACE")
    if _DET_MULTI_SP.search(raw):
        issues.append("MULTI_SPACE")
    if _eq_spacing_bad(raw):
        issues.append("EQ_SPACING")
    t = raw.strip()
    if t.startswith("=") or t.endswith("="):
        issues.append("DANGLING_EQ")
    if _DET_NON_WL_FULL.search(raw):
        issues.append("NON_WHITELIST")
    if "=" not in raw:
        issues.append("NO_SEPARATOR")

    return issues


def non_whitelist_codepoints(raw: str) -> list[str]:
    """
    Возвращает список кодпоинтов (с именами) вне белого списка (весь набор, не только NWL-детектор).
    Используется для статистики NON_WHITELIST.
    """
    result = []
    for ch in raw:
        cp = ord(ch)
        in_wl = (0x0020 <= cp <= 0x007E) or cp == 0x00B0 or (0x0400 <= cp <= 0x04FF) or cp == 0x2116
        if not in_wl:
            try:
                name = unicodedata.name(ch)
            except ValueError:
                name = "UNKNOWN"
            result.append(f"U+{cp:04X} {name}")
    return result


def display_raw(s: str) -> str:
    """Экранирует непечатаемые и управляющие символы как \\uXXXX для безопасного вывода."""
    out = []
    for ch in s:
        cp = ord(ch)
        if cp < 0x20 or (0x7F <= cp <= 0x9F) or unicodedata.category(ch) in ("Cf", "Cc", "Cn"):
            out.append(f"\\u{cp:04X}")
        else:
            out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def make_id(shifr, dopshifr) -> str:
    """Нормализованный ID из Шифр/ДопШифр. Делегирует в services.id_utils.make_norm_id."""
    return make_norm_id(shifr, dopshifr or "")


def _print_sample(items: list, show_all: bool, limit: int) -> None:
    cap = len(items) if show_all else min(limit, len(items))
    for row in items[:cap]:
        print(row)
    if not show_all and len(items) > cap:
        print(f"{PREFIX}    ... и ещё {len(items) - cap} (используйте --show-lists для полного вывода)")


# Категории, которые normalizeRoute ИСПРАВЛЯЕТ (для сверки)
_FIXABLE = {
    "EDGE_SPACE", "INVISIBLE", "CONTROL", "SMART_DASH", "SMART_QUOTE",
    "ELLIPSIS", "NON_NFC", "WEIRD_SPACE", "MULTI_SPACE",
    "EQ_SPACING", "DANGLING_EQ", "NON_WHITELIST",
}

# Категории, которые NOT исправляются (информационные)
_INFO_ONLY = {"NO_SEPARATOR", "EMPTY"}

# Человекочитаемые описания
_ISSUE_LABELS: dict[str, str] = {
    "EMPTY":         "Пустое значение",
    "EDGE_SPACE":    "Пробелы по краям",
    "INVISIBLE":     "Невидимые/zero-width символы",
    "CONTROL":       "Управляющие символы C0/C1",
    "SMART_DASH":    "«Умное» тире Word (не ASCII-дефис)",
    "SMART_QUOTE":   "«Умные» кавычки Word",
    "ELLIPSIS":      "Многоточие (U+2026, не ...)",
    "NON_NFC":       "Не-NFC (разложенная диакритика)",
    "WEIRD_SPACE":   "Нестандартный пробел (NBSP, таб, перенос и т.д.)",
    "MULTI_SPACE":   "Несколько пробелов подряд",
    "EQ_SPACING":    "Разделитель «=» оформлен неверно или дубли",
    "DANGLING_EQ":   "Висячий «=» в начале/конце",
    "NON_WHITELIST": "Символы вне белого списка (эмодзи, стрелки и т.п.)",
    "NO_SEPARATOR":  "Нет разделителя «=» (валидационная ошибка)",
}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Анализ проблем поля «Маршрут» в tlib.db (read-only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
        help="Вывести все затронутые отчёты вместо первых --limit строк.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=SAMPLE_LIMIT,
        help=f"Максимум строк в списке отчётов (по умолчанию: {SAMPLE_LIMIT}).",
    )
    parser.add_argument(
        "--csv",
        metavar="PATH",
        default=None,
        help="Экспортировать затронутые отчёты в CSV-файл.",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        print(f"{PREFIX} [ERROR] БД не найдена: {db_path.resolve()}", file=sys.stderr)
        sys.exit(1)

    # --- Загрузка данных ---
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT rowid, Шифр, ДопШифр, Маршрут FROM tlib ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()

    total = len(rows)
    total_nonempty = sum(1 for r in rows if (r["Маршрут"] or "").strip())

    # --- Анализ ---
    issue_counter: Counter = Counter()        # code -> кол-во отчётов
    nwl_char_counter: Counter = Counter()     # "U+XXXX NAME" -> кол-во отчётов
    affected: list[dict] = []
    warn_mismatch: list[str] = []

    for row in rows:
        raw: str = row["Маршрут"] or ""
        rid = row["rowid"]
        report_id = make_id(row["Шифр"], row["ДопШифр"])

        issues = detect_issues(raw)

        if not issues:
            # Дополнительная сверка: normalizeRoute должна вернуть то же значение
            normed = normalize_route(raw)
            if normed != raw:
                warn_mismatch.append(
                    f"  rowid={rid} ({report_id}): детекторы не нашли проблем, но normalize изменил значение"
                )
            continue

        for code in issues:
            issue_counter[code] += 1

        # NON_WHITELIST: собираем конкретные символы
        if "NON_WHITELIST" in issues:
            seen_in_row: set[str] = set()
            for cp_label in non_whitelist_codepoints(raw):
                if cp_label not in seen_in_row:
                    nwl_char_counter[cp_label] += 1
                    seen_in_row.add(cp_label)

        # Сверка fixable-детекторов с normalize
        fixable_found = bool(set(issues) & _FIXABLE)
        normed = normalize_route(raw)
        if fixable_found != (normed != raw):
            warn_mismatch.append(
                f"  rowid={rid} ({report_id}): рассогласование детекторов и normalize "
                f"(issues={issues}, changed={normed != raw})"
            )

        affected.append({
            "rowid":      rid,
            "id":         report_id,
            "shifr":      row["Шифр"],
            "dopshifr":   row["ДопШифр"] or "",
            "issues":     issues,
            "raw":        raw,
            "normalized": normed,
        })

    # --- Печать отчёта ---
    print(f"{PREFIX} База данных: {db_path.resolve()}")
    print(f"{PREFIX} Всего строк в tlib:          {total}")
    print(f"{PREFIX} Из них с непустым Маршрут:   {total_nonempty}")
    print()

    # Сводная статистика
    total_affected = len(affected)
    print(f"{PREFIX} {'='*60}")
    print(f"{PREFIX} СВОДНАЯ СТАТИСТИКА ПРОБЛЕМ")
    print(f"{PREFIX} {'='*60}")
    print(f"{PREFIX} Всего затронутых отчётов: {total_affected} из {total} ({100*total_affected/total:.1f}%)")
    print()

    if issue_counter:
        max_len = max(len(_ISSUE_LABELS.get(c, c)) for c in issue_counter)
        for code, cnt in issue_counter.most_common():
            label = _ISSUE_LABELS.get(code, code)
            fixable_mark = "" if code in _INFO_ONLY else " [исправляется]"
            print(f"{PREFIX}   {label:<{max_len}}  {cnt:>6} отчётов{fixable_mark}")
    else:
        print(f"{PREFIX}   Проблем не обнаружено.")
    print()

    # Топ символов NON_WHITELIST
    if nwl_char_counter:
        print(f"{PREFIX} {'='*60}")
        print(f"{PREFIX} ТОП СИМВОЛОВ ВНЕ БЕЛОГО СПИСКА (NON_WHITELIST)")
        print(f"{PREFIX} {'='*60}")
        for cp_label, cnt in nwl_char_counter.most_common(20):
            print(f"{PREFIX}   {cp_label:<50} — {cnt} отч.")
        if len(nwl_char_counter) > 20:
            print(f"{PREFIX}   ... и ещё {len(nwl_char_counter) - 20} уникальных символов")
        print()

    # Список затронутых отчётов
    if affected:
        print(f"{PREFIX} {'='*60}")
        print(f"{PREFIX} ЗАТРОНУТЫЕ ОТЧЁТЫ")
        print(f"{PREFIX} {'='*60}")
        lines = []
        for a in affected:
            codes_str = ", ".join(a["issues"])
            raw_disp  = display_raw(a["raw"])
            norm_disp = display_raw(a["normalized"])
            lines.append(
                f"{PREFIX}   rowid={a['rowid']:<6} {a['id']:<12} [{codes_str}]\n"
                f"{PREFIX}     было:   {raw_disp}\n"
                f"{PREFIX}     станет: {norm_disp}"
            )
        _print_sample(lines, args.show_lists, args.limit)
        print()

    # Предупреждения сверки
    if warn_mismatch:
        print(f"{PREFIX} {'='*60}")
        print(f"{PREFIX} ПРЕДУПРЕЖДЕНИЯ (рассогласование детекторов и normalize):")
        for w in warn_mismatch:
            print(f"{PREFIX} {w}")
        print()

    # CSV-экспорт
    if args.csv and affected:
        csv_path = Path(args.csv)
        with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["rowid", "shifr", "dopshifr", "id", "issues", "raw", "normalized"],
            )
            writer.writeheader()
            for a in affected:
                writer.writerow({
                    "rowid":      a["rowid"],
                    "shifr":      a["shifr"],
                    "dopshifr":   a["dopshifr"],
                    "id":         a["id"],
                    "issues":     "; ".join(a["issues"]),
                    "raw":        a["raw"],
                    "normalized": a["normalized"],
                })
        print(f"{PREFIX} CSV сохранён: {csv_path.resolve()} ({len(affected)} строк)")

    if not affected:
        print(f"{PREFIX} Проблем с полем «Маршрут» не обнаружено.")


if __name__ == "__main__":
    main()
