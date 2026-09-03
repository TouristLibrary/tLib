# Version 1.2 - 16.06.2026 21:00:00 GMT
# Sitemap Router для TlibWebApp
# Описание: Генерирует sitemap.xml с URL главной, about.html и всех отчётов.
#           URL отчётов строятся через build_canonical_query (services/seo/report_seo.py):
#             каноническая форма совпадает с canonical-тегом и share-URL из JS.
#           Кеш готового XML, инвалидация по app.state.reference_version
#             (обновляется в services/database/update_service.py при замене БД).
#           Кириллический ДопШифр URL-кодируется urllib.parse.quote; URL XML-экранируется.
#           lastmod берётся из поля ДатаВремяЗагрузки (ISO datetime, дата-часть);
#             если поле пустое или непарсимое — lastmod пропускается (guard).
# 1.1: подключение к БД переведено на open_tlib_db() (read-only).
# 1.2: добавлен <lastmod> из ДатаВремяЗагрузки.

import urllib.parse
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, Request
from starlette.responses import Response

from config import DATABASE_TABLE_NAME, SITE_URL
from services.database import open_tlib_db
from services.seo.report_seo import build_canonical_query
from logging_config import app_logger

router = APIRouter(tags=["seo"])

_sitemap_cache: str | None = None
_sitemap_cache_version: str | None = None


@router.get("/sitemap.xml")
async def sitemap_xml(request: Request):
    """
    Генерирует sitemap.xml.
    Кеш инвалидируется при изменении app.state.reference_version.
    """
    global _sitemap_cache, _sitemap_cache_version

    current_version = getattr(request.app.state, "reference_version", None)
    if _sitemap_cache is not None and _sitemap_cache_version == current_version:
        return Response(content=_sitemap_cache, media_type="application/xml")

    base = (SITE_URL or "").rstrip("/")

    urls = [
        f"{base}/",
        f"{base}/about.html",
    ]

    # Записи без lastmod для главной и about.html
    static_entries = "\n".join(
        f"  <url><loc>{xml_escape(u)}</loc></url>" for u in urls
    )

    report_entries_parts: list[str] = []
    try:
        conn = open_tlib_db()
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT Шифр, ДопШифр, ДатаВремяЗагрузки FROM {DATABASE_TABLE_NAME}"
        )
        rows = cursor.fetchall()
        conn.close()

        for row in rows:
            canonical_q = build_canonical_query(dict(row))
            encoded_q = urllib.parse.quote(canonical_q, safe="-")
            loc = xml_escape(f"{base}/?{encoded_q}")
            # Guard: берём только первые 10 символов ISO-даты (YYYY-MM-DD)
            raw_date = (row["ДатаВремяЗагрузки"] or "").strip()
            lastmod = raw_date[:10] if len(raw_date) >= 10 and raw_date[4] == "-" else ""
            if lastmod:
                report_entries_parts.append(
                    f"  <url><loc>{loc}</loc><lastmod>{xml_escape(lastmod)}</lastmod></url>"
                )
            else:
                report_entries_parts.append(f"  <url><loc>{loc}</loc></url>")

    except Exception as exc:
        app_logger.error(f"sitemap_xml: ошибка чтения БД: {exc}", exc_info=True)

    all_entries = static_entries
    if report_entries_parts:
        all_entries += "\n" + "\n".join(report_entries_parts)

    xml_content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{all_entries}\n"
        "</urlset>"
    )

    _sitemap_cache = xml_content
    _sitemap_cache_version = current_version

    return Response(content=xml_content, media_type="application/xml")
