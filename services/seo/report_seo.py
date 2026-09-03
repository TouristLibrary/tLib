# Version 1.4 - 16.06.2026 22:00:00 GMT
# SEO-модуль для TlibWebApp
# Описание: Единственный источник истины «компактный URL отчёта».
# 1.4: единый источник title/description — HTML-шаблоны (index.html, about.html);
#           питоновские константы _HOMEPAGE_*/_ABOUT_* удалены.
#           _extract_title_description — извлечение заголовка и описания из шаблона.
#           _build_og_image_meta — og:image + og:image:width + og:image:height;
#           OG_IMAGE_PATH/OG_IMAGE_WIDTH/OG_IMAGE_HEIGHT вынесены в config.
# 1.3: render_homepage_html — теперь подставляет canonical + Open Graph в SEO_HEAD (не отдаёт шаблон как есть).
#           render_about_html — новая функция для about.html с canonical + OG + description.
#           _build_seo_head_html — добавлены og: мета-теги для отчётов.
#           build_route_html — добавлена microdata schema.org/CreativeWork (невидимые <meta itemprop>).
# 1.2: удалена неиспользуемая invalidate_template_cache() — кеш живёт до перезапуска сервера.
#           parse_report_query — распознаёт компактный URL (regex + паддинг),
#             работает по декодированной query-строке целиком; ДопШифр передаётся
#             без нормализации — регистронезависимость обеспечивает fetch_report_row.
#           fetch_report_row — SELECT из БД с нормализацией (Шифр как число, ДопШифр через
#             UDF LOWER — питоновская lambda, поддерживает кириллицу; паттерн из search_router).
#           build_canonical_query — каноническая форма URL (Шифр без паддинга + ДопШифр как в БД),
#             совпадает с share-URL из JS buildLinkText. Апкейс не применяется — берётся
#             авторитетное значение из БД (для legacy-записей вроде '1-ш' это важно).
#           build_title/build_description/build_route_html — мирроринг renderSingleResultFormatted
#             (js/modules/ui/results/single.js) и форматтеров DataFormatter (js/modules/ui/dataFormatter.js).
#           render_report_html — подстановка SEO_HEAD и SEO_CONTENT зон для отчёта.
#           Использует SITE_URL из config для canonical и sitemap (та же константа, что и в auth/notify).
#           Все данные из БД экранируются через html.escape (защита от XSS).

import html
import re
import sqlite3
import urllib.parse
from pathlib import Path

from config import (
    DATABASE_PATH, DATABASE_TABLE_NAME, SITE_URL,
    OG_IMAGE_PATH, OG_IMAGE_WIDTH, OG_IMAGE_HEIGHT,
)

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

MONTHS_RU = [
    "янв", "фев", "мар", "апр", "май", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
]

_REPORT_QUERY_RE = re.compile(r'^(\d+)(?:-(.+))?$')

# Паттерны для замены зон в шаблоне
_SEO_HEAD_RE = re.compile(r'<!--SEO_HEAD-->.*?<!--/SEO_HEAD-->', re.DOTALL)
_SEO_CONTENT_RE = re.compile(r'<!--SEO_CONTENT-->.*?<!--/SEO_CONTENT-->', re.DOTALL)

# Паттерны для извлечения title/description из HTML-шаблона
_TITLE_RE = re.compile(r'<title>(.*?)</title>', re.DOTALL)
_DESC_RE = re.compile(r'<meta name="description" content="(.*?)">', re.DOTALL)


def _extract_title_description(template: str) -> tuple[str, str]:
    """
    Извлекает заголовок и описание из HTML-шаблона (единственный источник истины).
    Применяет html.unescape, чтобы вернуть чистый текст без HTML-сущностей.
    Вызывается до _SEO_HEAD_RE.sub(), пока зона SEO_HEAD содержит исходные теги.
    """
    t = _TITLE_RE.search(template)
    d = _DESC_RE.search(template)
    title = html.unescape(t.group(1).strip()) if t else ""
    description = html.unescape(d.group(1).strip()) if d else ""
    return title, description

# ---------------------------------------------------------------------------
# Кеш шаблонов
# ---------------------------------------------------------------------------

_template: str | None = None
_homepage_cache: bytes | None = None
_about_template: str | None = None
_about_cache: bytes | None = None


def _get_template() -> str:
    global _template
    if _template is None:
        _template = Path("index.html").read_text(encoding="utf-8")
    return _template


def _get_about_template() -> str:
    global _about_template
    if _about_template is None:
        _about_template = Path("about.html").read_text(encoding="utf-8")
    return _about_template


# ---------------------------------------------------------------------------
# Парсинг и нормализация URL
# ---------------------------------------------------------------------------

def parse_report_query(raw_query: str) -> tuple[str, str] | None:
    """
    Распознаёт компактный URL отчёта: '123' или '123-тСсР'.

    Принимает сырую (возможно, %-кодированную) query-строку целиком
    (request.url.query — без ведущего '?'). Это покрывает запросы вида
    /?123-%D0%A2%D0%A1%D0%A1%D0%A0 от краулеров.

    Возвращает (shifr5, dopshifr) или None.
    shifr5 — шифр с паддингом до 5 цифр (для поиска в БД).
    dopshifr — ДопШифр как введён (без нормализации регистра);
               регистронезависимое сравнение с кириллицей — в fetch_report_row.
    """
    decoded = urllib.parse.unquote(raw_query or "")
    m = _REPORT_QUERY_RE.match(decoded)
    if not m:
        return None
    shifr5 = m.group(1).zfill(5)
    dopshifr = (m.group(2) or "").strip()
    return shifr5, dopshifr


# ---------------------------------------------------------------------------
# Запрос к БД
# ---------------------------------------------------------------------------

def fetch_report_row(shifr5: str, dopshifr: str) -> dict | None:
    """
    Получить строку отчёта из БД по Шифр + ДопШифр.

    Нормализация: Шифр сравнивается как INTEGER (паддинг не важен),
    ДопШифр — через LOWER(COALESCE(..., '')); используется питоновская UDF LOWER,
    которая корректно складывает кириллицу (паттерн из search_router/reference_loader).
    Соединение открывается на один запрос (как в остальных роутерах).
    """
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        conn.create_function("LOWER", 1, lambda s: s.lower() if s else s)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        shifr_int = int(shifr5)
        cursor.execute(
            f"SELECT Маршрут, Район, РайонОбщий, Тип, ТипСудна, "
            f"КатегорияС, КатегорияПо, Год, МесяцС, МесяцПо, "
            f"Автор, Город, Комментарии, Шифр, ДопШифр "
            f"FROM {DATABASE_TABLE_NAME} "
            f"WHERE CAST(Шифр AS INTEGER) = ? "
            f"AND LOWER(COALESCE(ДопШифр, '')) = LOWER(?)",
            (shifr_int, dopshifr),
        )
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row is not None else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Канонический URL
# ---------------------------------------------------------------------------

def build_canonical_query(row: dict) -> str:
    """
    Каноническая форма query отчёта: '{Шифр без паддинга}[-{ДопШифр как в БД}]'.

    ДопШифр берётся из БД как есть (без апкейса) — совпадает с JS buildLinkText
    (js/modules/ui/dataFormatter.js), который тоже использует сырое значение из БД.
    Для нормализованных данных (ДопШифр UPPER) результат тот же;
    для legacy-записей в нижнем регистре (например '1-ш') canonical корректен.
    Апкейс применяется только в parse_report_query для регистронезависимого поиска.
    Используется в canonical-теге, видимом блоке, title и sitemap — все три совпадают.
    """
    shifr = str(int(str(row["Шифр"])))
    dop = (row.get("ДопШифр") or "").strip()
    return f"{shifr}-{dop}" if dop else shifr


# ---------------------------------------------------------------------------
# Форматирование полей (мирроринг JS DataFormatter)
# ---------------------------------------------------------------------------

def _format_category(row: dict) -> str:
    """Мирроринг DataFormatter.formatCategory."""
    from_cat = str(row.get("КатегорияС") or "").strip()
    to_cat = str(row.get("КатегорияПо") or "").strip()
    if from_cat and to_cat:
        return f"{from_cat} - {to_cat}"
    return from_cat or to_cat


def _format_region(row: dict) -> str:
    """Мирроринг DataFormatter.formatRegion (без html.escape — только текст)."""
    obshiy = str(row.get("РайонОбщий") or "").strip()
    rayon = str(row.get("Район") or "").strip()
    if obshiy and rayon:
        return f"{obshiy}: {rayon}"
    return obshiy or rayon


def _format_year_month(row: dict) -> str:
    """Мирроринг DataFormatter.formatYearMonth."""
    year = str(row.get("Год") or "").strip()
    m_from = row.get("МесяцС")
    m_to = row.get("МесяцПо")
    month_from = MONTHS_RU[int(m_from) - 1] if m_from else ""
    month_to = MONTHS_RU[int(m_to) - 1] if m_to else ""
    if month_from and month_to and month_from != month_to:
        month_str = f"{month_from}-{month_to}"
    else:
        month_str = month_from or month_to
    if year and month_str:
        return f"{year}, {month_str}"
    return year or month_str


# ---------------------------------------------------------------------------
# Метатеги
# ---------------------------------------------------------------------------

def build_title(row: dict) -> str:
    """
    Заголовок страницы отчёта. До ~60 символов.
    Шаблон: Отчёт {Шифр}[-{ДопШифр}]: {Тип} поход {Категория} к.с., {Район}, {Год}
    Пустые поля пропускаются вместе с разделителями.
    """
    canonical = build_canonical_query(row)
    typ = str(row.get("Тип") or "").strip()
    cat = _format_category(row)
    region = _format_region(row)
    god = str(row.get("Год") or "").strip()

    parts = []
    if typ:
        parts.append(f"{typ} поход {cat} к.с." if cat else f"{typ} поход")
    elif cat:
        parts.append(f"{cat} к.с.")
    if region:
        parts.append(region)
    if god:
        parts.append(god)

    if parts:
        return f"Отчёт {canonical}: {', '.join(parts)}"
    return f"Отчёт {canonical}"


def _truncate_at_word(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    truncated = text[: max_len - 1]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated + "…"


def build_description(row: dict, max_len: int = 160) -> str:
    """
    Мета-описание страницы отчёта. Максимум 160 символов, обрезка по границе слова.
    Шаблон (без склонений, KISS):
      Отчёт {Шифр}[-{ДопШифр}]: {Тип} поход, {Категория} к.с., {Район}, {Год} г.
      Автор: {Автор}. Маршрут: {Маршрут}
    «Маршрут» — последним, занимает весь остаток лимита: гарантирует уникальность
    для отчётов с одинаковыми районом/годом.
    """
    canonical = build_canonical_query(row)
    typ = str(row.get("Тип") or "").strip()
    cat = _format_category(row)
    region = _format_region(row)
    god = str(row.get("Год") or "").strip()
    avtor = str(row.get("Автор") or "").strip()
    marshrut = str(row.get("Маршрут") or "").strip()

    inner = []
    if typ:
        inner.append(f"{typ} поход, {cat} к.с." if cat else f"{typ} поход")
    elif cat:
        inner.append(f"{cat} к.с.")
    if region:
        inner.append(region)
    if god:
        inner.append(f"{god} г.")

    prefix = f"Отчёт {canonical}:"
    if inner:
        prefix += " " + ", ".join(inner)
    if avtor:
        prefix += f" Автор: {avtor}."

    if marshrut:
        route_label = " Маршрут: "
        avail = max_len - len(prefix) - len(route_label) - 1  # 1 для «…»
        if avail >= len(marshrut):
            return prefix + route_label + marshrut
        if avail > 5:
            trunc = marshrut[:avail]
            last_space = trunc.rfind(" ")
            if last_space > 0:
                trunc = trunc[:last_space]
            return prefix + route_label + trunc + "…"

    return _truncate_at_word(prefix, max_len)


# ---------------------------------------------------------------------------
# Видимый серверный блок отчёта (мирроринг renderSingleResultFormatted)
# ---------------------------------------------------------------------------

def build_route_html(row: dict) -> str:
    """
    Видимый HTML-блок отчёта: зеркалит разметку renderSingleResultFormatted
    (js/modules/ui/results/single.js, строки 154–226).
    Без табов, иконок и блока «загрузил» — только текстовые поля.
    Разделитель строки 2: ' ; ' (как в JS).
    Все данные экранированы html.escape.
    """
    canonical = build_canonical_query(row)
    marshrut = str(row.get("Маршрут") or "").strip()
    typ = str(row.get("Тип") or "").strip()
    tip_sudna = str(row.get("ТипСудна") or "").strip()
    cat = _format_category(row)
    region = _format_region(row)
    ym = _format_year_month(row)
    avtor = str(row.get("Автор") or "").strip()
    gorod = str(row.get("Город") or "").strip()
    comments = str(row.get("Комментарии") or "").strip()

    # Строка 1: #canonical маршрут
    first_line = f"#{canonical}"
    if marshrut:
        first_line += f" {marshrut}"

    # Строка 2: район ; тип+категория ; год+месяц ; автор
    second_parts = []
    if region:
        second_parts.append(region)

    type_cat = ""
    if typ:
        type_cat = f"{typ} ({tip_sudna})" if tip_sudna else typ
        if cat:
            type_cat += f" {cat} к.с."
    elif cat:
        type_cat = f"{cat} к.с."
    if type_cat:
        second_parts.append(type_cat)

    if ym:
        second_parts.append(ym)

    if avtor and gorod:
        second_parts.append(f"{avtor} ({gorod})")
    elif avtor:
        second_parts.append(avtor)
    elif gorod:
        second_parts.append(gorod)

    lines_html = (
        f'<div class="single-result-field route-field">'
        f'<span>{html.escape(first_line)}</span></div>\n'
    )
    if second_parts:
        lines_html += (
            f'<div class="single-result-field">'
            f'<span>{html.escape(" ; ".join(second_parts))}</span></div>\n'
        )
    if comments:
        lines_html += (
            f'<div class="single-result-field comments-field">'
            f'<span>{html.escape(comments)}</span></div>\n'
        )

    # Невидимые microdata-поля schema.org/CreativeWork (не затрагивают верстку)
    meta_name = marshrut or f"Отчёт {canonical}"
    meta_parts = [f'<meta itemprop="name" content="{html.escape(meta_name)}">']
    if avtor:
        meta_parts.append(f'<meta itemprop="author" content="{html.escape(avtor)}">')
    god_str = str(row.get("Год") or "").strip()
    if god_str:
        meta_parts.append(f'<meta itemprop="dateCreated" content="{html.escape(god_str)}">')
    region = _format_region(row)
    if region:
        meta_parts.append(f'<meta itemprop="spatialCoverage" content="{html.escape(region)}">')
    if comments:
        meta_parts.append(f'<meta itemprop="description" content="{html.escape(comments)}">')
    meta_html = "\n".join(meta_parts)

    return (
        f'<div class="single-result-formatted"'
        f' itemscope itemtype="https://schema.org/CreativeWork">\n'
        f'{meta_html}\n{lines_html}</div>'
    )


# ---------------------------------------------------------------------------
# Рендер HTML
# ---------------------------------------------------------------------------

def _build_og_image_meta(base: str) -> str:
    """Блок og:image (URL + размеры) на основе SITE_URL и OG_IMAGE_* из config."""
    img = html.escape(f"{base}{OG_IMAGE_PATH}")
    return (
        f'<meta property="og:image" content="{img}">\n'
        f'    <meta property="og:image:width" content="{OG_IMAGE_WIDTH}">\n'
        f'    <meta property="og:image:height" content="{OG_IMAGE_HEIGHT}">'
    )


def _build_seo_head_html(row: dict) -> str:
    """SEO_HEAD зона для страницы отчёта: title + description + canonical + Open Graph."""
    base = (SITE_URL or "").rstrip("/")
    canonical_q = build_canonical_query(row)
    encoded_q = urllib.parse.quote(canonical_q, safe="-")
    canonical_url = html.escape(f"{base}/?{encoded_q}")
    og_image = _build_og_image_meta(base)
    title = html.escape(build_title(row))
    description = html.escape(build_description(row))
    return (
        f"<title>{title}</title>\n"
        f'    <meta name="description" content="{description}">\n'
        f'    <link rel="canonical" href="{canonical_url}">\n'
        f'    <meta property="og:type" content="article">\n'
        f'    <meta property="og:title" content="{title}">\n'
        f'    <meta property="og:description" content="{description}">\n'
        f'    <meta property="og:url" content="{canonical_url}">\n'
        f'    {og_image}'
    )


def _build_homepage_seo_head(title: str, description: str) -> str:
    """
    SEO_HEAD зона главной страницы: title + description + canonical + Open Graph.
    title и description передаются извне (извлечены из шаблона index.html).
    """
    base = (SITE_URL or "").rstrip("/")
    canonical = html.escape(f"{base}/")
    og_image = _build_og_image_meta(base)
    title_esc = html.escape(title)
    description_esc = html.escape(description)
    return (
        f"<title>{title_esc}</title>\n"
        f'    <meta name="description" content="{description_esc}">\n'
        f'    <link rel="canonical" href="{canonical}">\n'
        f'    <link itemprop="url" href="{canonical}">\n'
        f'    <meta property="og:type" content="website">\n'
        f'    <meta property="og:title" content="{title_esc}">\n'
        f'    <meta property="og:description" content="{description_esc}">\n'
        f'    <meta property="og:url" content="{canonical}">\n'
        f'    {og_image}'
    )


def render_homepage_html() -> bytes:
    """
    Возвращает кешированные байты index.html с canonical + Open Graph в SEO_HEAD.
    title/description извлекаются из index.html (единственный источник истины).
    Кеш действителен до перезапуска (SITE_URL фиксирован в рантайме).
    """
    global _homepage_cache
    if _homepage_cache is None:
        tmpl = _get_template()
        title, description = _extract_title_description(tmpl)
        seo_head = _build_homepage_seo_head(title, description)
        rendered = _SEO_HEAD_RE.sub(
            f"<!--SEO_HEAD-->{seo_head}<!--/SEO_HEAD-->",
            tmpl,
        )
        _homepage_cache = rendered.encode("utf-8")
    return _homepage_cache


def render_about_html() -> bytes:
    """
    Возвращает кешированные байты about.html с canonical + Open Graph в SEO_HEAD.
    title/description извлекаются из about.html (единственный источник истины).
    Кеш действителен до перезапуска (SITE_URL фиксирован в рантайме).
    """
    global _about_cache
    if _about_cache is None:
        tmpl = _get_about_template()
        title, description = _extract_title_description(tmpl)
        base = (SITE_URL or "").rstrip("/")
        canonical = html.escape(f"{base}/about.html")
        og_image = _build_og_image_meta(base)
        title_esc = html.escape(title)
        description_esc = html.escape(description)
        seo_head = (
            f"<title>{title_esc}</title>\n"
            f'    <meta name="description" content="{description_esc}">\n'
            f'    <link rel="canonical" href="{canonical}">\n'
            f'    <meta property="og:type" content="website">\n'
            f'    <meta property="og:title" content="{title_esc}">\n'
            f'    <meta property="og:description" content="{description_esc}">\n'
            f'    <meta property="og:url" content="{canonical}">\n'
            f'    {og_image}'
        )
        rendered = _SEO_HEAD_RE.sub(
            f"<!--SEO_HEAD-->{seo_head}<!--/SEO_HEAD-->",
            tmpl,
        )
        _about_cache = rendered.encode("utf-8")
    return _about_cache


def render_report_html(row: dict) -> str:
    """
    Рендерит index.html с per-report SEO-метатегами и видимым блоком маршрута.
    SEO_HEAD → title/description/canonical.
    SEO_CONTENT → видимый div.single-result-formatted (без кеша, на лету).
    """
    tmpl = _get_template()

    seo_head = _build_seo_head_html(row)
    route_html = build_route_html(row)

    result = _SEO_HEAD_RE.sub(
        f"<!--SEO_HEAD-->{seo_head}<!--/SEO_HEAD-->",
        tmpl,
    )
    result = _SEO_CONTENT_RE.sub(
        f"<!--SEO_CONTENT-->{route_html}<!--/SEO_CONTENT-->",
        result,
    )
    return result
