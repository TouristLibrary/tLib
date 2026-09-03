# Version 1.1 - 19.02.2026 00:00:00 GMT
# HTTP Cache Utilities для TlibWebApp
# Описание: Общие утилиты для HTTP кеш-валидаторов (ETag / Last-Modified / условные запросы).
#           Используется в archive_router и pdf_router для поддержки ETag, Last-Modified и 304.
#           check_not_modified() — единая точка входа для условных запросов: строит common_headers
#           и возвращает ранний Response(304/200) или None если нужно формировать полный ответ.

from pathlib import Path
from email.utils import formatdate, parsedate_to_datetime
from datetime import timezone

from fastapi import Request
from fastapi.responses import Response

from config import CACHE_CONTROL_REVALIDATE


def get_path_signature(path: Path) -> tuple:
    """
    Возвращает подпись файла для кеш-валидаторов.

    Returns:
        (mtime_ns, size, mtime_seconds)
    """
    st = path.stat()
    mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))
    size = int(st.st_size)
    mtime_s = int(st.st_mtime)
    return mtime_ns, size, mtime_s


def http_last_modified(mtime_seconds: int) -> str:
    """Форматирует Last-Modified в RFC 1123 (GMT)."""
    return formatdate(timeval=mtime_seconds, usegmt=True)


def if_none_match_matches(if_none_match_raw, etag: str) -> bool:
    """
    Проверяет совпадение If-None-Match с нашим ETag.

    Поддерживает:
    - "*"
    - список ETag через запятую
    """
    if not if_none_match_raw:
        return False
    raw = str(if_none_match_raw).strip()
    if raw == "*":
        return True
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return etag in parts


def if_modified_since_allows_304(if_modified_since_raw, last_modified_seconds: int) -> bool:
    """
    Если If-Modified-Since >= Last-Modified, можно отвечать 304.
    """
    if not if_modified_since_raw:
        return False
    try:
        dt = parsedate_to_datetime(str(if_modified_since_raw))
        if dt is None:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        ims_s = int(dt.timestamp())
        return ims_s >= int(last_modified_seconds)
    except Exception:
        return False


def check_not_modified(request: Request, etag: str, mtime_s: int) -> tuple[Response | None, dict]:
    """
    Проверяет условные заголовки запроса и формирует common_headers для кеширования.

    Используется в роутерах вместо повторяющегося блока If-None-Match / If-Modified-Since.

    Args:
        request: HTTP запрос
        etag:    сформированный ETag для ресурса
        mtime_s: время последней модификации ресурса (unix seconds)

    Returns:
        (early_response, common_headers)
        - early_response: Response(304) при not-modified, Response(200/304) при HEAD,
          или None если нужно формировать полный ответ.
        - common_headers: dict с Cache-Control / ETag / Last-Modified;
          caller обязан выставить их на итоговый FileResponse.
    """
    common_headers = {
        "Cache-Control": CACHE_CONTROL_REVALIDATE,
        "ETag": etag,
        "Last-Modified": http_last_modified(mtime_s),
    }

    inm = request.headers.get("if-none-match")
    ims = request.headers.get("if-modified-since")
    not_modified = if_none_match_matches(inm, etag) or (
        not inm and if_modified_since_allows_304(ims, mtime_s)
    )

    if request.method == "HEAD":
        return Response(status_code=304 if not_modified else 200, headers=common_headers), common_headers
    if not_modified:
        return Response(status_code=304, headers=common_headers), common_headers

    return None, common_headers
