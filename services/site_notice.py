# Version 1.0 - 18.09.2026 07:50:00 GMT
# Сервис объявления в левой колонке главной страницы
# Описание: Читает короткий текст из data.secret/site_notice.json (не в git).
#           Пустой/отсутствующий/битый файл → слот скрыт. Ошибки не роняют /api/config.

from urllib.parse import urlsplit

from config import SITE_NOTICE_MAX_LENGTH, SITE_NOTICE_PATH
from logging_config import app_logger
from services.json_io import read_json

_EMPTY = {"text": "", "href": ""}
_ALLOWED_SCHEMES = ("http", "https")


def _empty() -> dict:
    return dict(_EMPTY)


def _safe_href(raw) -> str:
    """Возвращает href только для http(s) с хостом, иначе пустую строку."""
    if not isinstance(raw, str):
        return ""
    href = raw.strip()
    if not href:
        return ""
    try:
        parts = urlsplit(href)
    except ValueError:
        return ""
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        return ""
    if not parts.netloc:
        return ""
    return href


def _normalize(payload) -> dict:
    if not isinstance(payload, dict):
        app_logger.warning("Объявление сайта: ожидался JSON-объект")
        return _empty()

    raw_text = payload.get("text", "")
    if not isinstance(raw_text, str):
        app_logger.warning("Объявление сайта: поле text должно быть строкой")
        return _empty()

    text = raw_text.strip()
    if not text:
        return _empty()
    if len(text) > SITE_NOTICE_MAX_LENGTH:
        app_logger.warning(
            "Объявление сайта: text длиннее %s символов — скрыто",
            SITE_NOTICE_MAX_LENGTH,
        )
        return _empty()

    return {"text": text, "href": _safe_href(payload.get("href"))}


def load_site_notice() -> dict:
    """
    Возвращает {"text": str, "href": str} для клиента.
    Пустой text — объявления нет. Никогда не пробрасывает исключение наружу.
    """
    try:
        payload = read_json(SITE_NOTICE_PATH)
    except FileNotFoundError:
        return _empty()
    except Exception:
        app_logger.warning("Не удалось прочитать объявление сайта", exc_info=True)
        return _empty()

    try:
        return _normalize(payload)
    except Exception:
        app_logger.warning("Не удалось разобрать объявление сайта", exc_info=True)
        return _empty()
