# Version 1.0 - 10.07.2026 09:45:00 GMT
# Сервис «Скрытые отчёты» для TlibWebApp
# Описание: Список отчётов (Шифр-ДопШифр), временно скрытых администратором.
#           Скрытые отчёты остаются в поиске, но файл отчёта не кешируется и не
#           показывается — на странице отчёта видна только карточка с плашкой.
#           Хранится как одна настройка в app_settings (auth.db, писабельная БД),
#           т.к. tlib.db доступна API только на чтение (§3 инвариантов проекта).
#           Кэш в памяти — app.state.hidden_reports (set нормализованных ID).

import re

from services.auth.auth_db import get_setting
from services.id_utils import make_norm_id
from services.upload.upload_service import DOP_SHIFR_RE

HIDDEN_REPORTS_SETTING = "hidden_reports"

_SPLIT_RE = re.compile(r"[;,\s]+")


def parse_and_normalize(text: str) -> tuple[list[str], list[str]]:
    """
    Разбирает текст textarea на токены (разделители: ';', ',', пробел/перенос строки),
    нормализует каждый через make_norm_id (Шифр -> 5 цифр, ДопШифр -> UPPERCASE).

    Returns:
        (canonical_ids, invalid_tokens) — canonical_ids отсортированы и уникальны;
        invalid_tokens — исходные токены, не прошедшие разбор (для сообщения об ошибке).
    """
    tokens = [t for t in _SPLIT_RE.split((text or "").strip()) if t]
    canonical: set[str] = set()
    invalid: list[str] = []

    for token in tokens:
        shifr_part, _, dop_part = token.partition("-")
        if not shifr_part.isdigit():
            invalid.append(token)
            continue
        dop = dop_part.strip().upper()
        if dop and not DOP_SHIFR_RE.match(dop):
            invalid.append(token)
            continue
        try:
            canonical.add(make_norm_id(shifr_part, dop))
        except (ValueError, TypeError):
            invalid.append(token)

    return sorted(canonical), invalid


def format_for_storage(ids) -> str:
    """Готовит текст для сохранения/отображения в textarea: один ID на строку, по алфавиту."""
    return "\n".join(sorted(set(ids)))


def load_hidden_reports() -> set[str]:
    """Загружает set скрытых ID из app_settings (auth.db). Невалидные токены отбрасывает молча
    (в БД настройки попадает только уже провалидированный текст, см. admin_router)."""
    text = get_setting(HIDDEN_REPORTS_SETTING, "")
    canonical_ids, _invalid = parse_and_normalize(text)
    return set(canonical_ids)
