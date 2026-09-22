# Version 1.8 - 22.09.2026 13:57:00 GMT
# Static Router для TlibWebApp
# Описание: Роутер для обработки статических страниц, серверных редиректов и таблицы редиректов.
#           GET / — SEO-aware рендер: для компактных URL отчётов (/?123, /?123-ТССР) возвращает
#             per-report title/description/canonical + видимый блок маршрута из services/seo/report_seo.py;
#             для главной — кешированный шаблон; для фильтров/не найденных — шаблон + X-Robots-Tag: noindex.
#             Метки из ROBOTS_CLEAN_PARAMS (ysclid и др.) и хвостовой «=» у компактного
#             шифра (3725=) снимаются 301 на тот же URL без них. Legacy ?id= — до очистки.
#           GET /robots.txt — динамически генерирует robots.txt с актуальным SITE_URL в Sitemap и Clean-param.
#           GET /index.html — 301 редирект на / (устранение дубликата).
#           GET /about.html — рендер с canonical + OG через render_about_html().
#           Legacy-редиректы поддерживают форматы:
#             - /doc.aspx?id=<digits>[&page=<digits>] (и регистровые варианты: /Doc.aspx, /DOC.ASPX и др.)
#             - /?id=<digits>[&page=<digits>]
#             - /default.aspx (и регистровые варианты) → / или /?id= если есть id
#           Маппинг выполняется строго по таблице app.state.redirect_table: id=<СтарыйID> → <Шифр>-<ДопШифр> (или <Шифр>).
#           Если указан page, добавляет hash #tab=pdf&p=<page> для открытия PDF на нужной странице (PDF считается один).
#           Если id невалиден/не найден, редиректит на /?notfound=1 (UI показывает «Ничего не найдено»).
#           Редиректы статических директорий: /js, /css, /assets, /data → с добавлением / в конце.
#           Поддерживает fallback для favicon.ico (assets/favicon.ico → favicon.ico).
#           1.4: about.html через HTMLResponse (render_about_html); robots.txt + Clean-param;
#                защитные legacy-маршруты /doc.aspx (регистровые варианты) + /default.aspx.
#           1.5: robots.txt разрешает обход PDF (Allow: /api/pdf/, /data/*.pdf$) — краулер должен
#                скачать файл, чтобы увидеть X-Robots-Tag: noindex и убрать его из индекса.
#           1.6: GET / снимает метки ROBOTS_CLEAN_PARAMS (ysclid и др.) редиректом 301
#                на тот же URL без них — компактный адрес отчёта снова разбирается.
#           1.7: хвостовой «=» у компактного шифра (3725= от Яндекса) тоже снимается 301;
#                legacy ?id= обрабатывается до очистки меток — один переход, не два.
#           1.8: /, /index.html, /about.html и /robots.txt принимают HEAD — краулеры
#                (curl -I) больше не получают 405 от FastAPI, который HEAD к GET не добавляет.

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pathlib import Path
import urllib.parse

# Импорт конфигурации
from config import (
    FAVICON_PATH,
    LOCAL_ARCHIVE_PATH,
    REDIRECT_SOURCE,
    REDIRECT_SOURCE_ALIASES,
    REDIRECT_DEFAULT_ASPX_PATHS,
    REDIRECT_STATUS_CODE,
    ROBOTS_CLEAN_PARAMS,
    SITE_URL,
    STATIC_REDIRECT_STATUS_CODE,
    LEGACY_REDIRECT_STATUS_CODE,
)

# Импорт логгеров
from logging_config import app_logger

# Импорт SEO-модуля
from services.seo.report_seo import (
    parse_report_query,
    fetch_report_row,
    render_homepage_html,
    render_about_html,
    render_report_html,
)


# Создаем роутер без prefix (корневые маршруты)
router = APIRouter(tags=["static"])

# Имена меток из Clean-param: сравнение ключа query без учёта регистра
_TRACKING_PARAM_NAMES: frozenset[str] = frozenset(
    name.lower() for name in ROBOTS_CLEAN_PARAMS.split("&") if name
)


def _strip_tracking_query(raw_query: str) -> str | None:
    """
    Убирает из сырой query метки из ROBOTS_CLEAN_PARAMS (ysclid, utm и т.п.)
    и хвостовой «=» у первого сегмента, если это компактный шифр без значения
    (Яндекс пересобирает /?3725 в /?3725=, когда дописывает ysclid).

    None — вырезать нечего, редирект не нужен.
    Пустая строка — в query были только метки, редирект на /.
    Оставшиеся сегменты возвращаются как были, без перекодирования.
    """
    if not raw_query:
        return None

    kept: list[str] = []
    stripped = False
    for index, segment in enumerate(raw_query.split("&")):
        key_raw, sep, value = segment.partition("=")
        key = urllib.parse.unquote(key_raw).lower()
        if key in _TRACKING_PARAM_NAMES:
            stripped = True
            continue
        # Только первый сегмент: 3725= и 3725-%D0%A2…= — шифр, а не пустой параметр
        if index == 0 and sep and value == "" and parse_report_query(key_raw) is not None:
            kept.append(key_raw)
            stripped = True
            continue
        kept.append(segment)

    if not stripped:
        return None
    return "&".join(kept)


def _build_canonical_redirect_url(redirect_target: str, page: int | None) -> str:
    """
    Формирует канонический URL редиректа на новый сайт.

    - redirect_target: строка вида "<Шифр>-<ДопШифр>" или "<Шифр>"
    - page: опциональная страница PDF (>= 1)

    Возвращает URL вида:
      "/?<encoded_target>[#tab=pdf&p=N]"
    """
    encoded_target = urllib.parse.quote(str(redirect_target), safe="-_.~")
    url = f"/?{encoded_target}"
    if isinstance(page, int) and page >= 1:
        url += f"#tab=pdf&p={page}"
    return url


def _parse_legacy_id_page(request: Request) -> tuple[str | None, int | None]:
    """
    Парсит legacy query параметры.

    По требованиям legacy-форматы содержат только id и опционально page, но мы работаем
    терпимо: если id присутствует — редиректим, игнорируя любые лишние параметры.

    Returns:
      (old_id, page)
      - old_id: строка с цифрами или None
      - page: int >= 1 или None
    """
    params = request.query_params
    old_id = (params.get("id") or "").strip()
    if not old_id:
        return None, None

    if not old_id.isdigit():
        return old_id, None

    page_raw = (params.get("page") or "").strip()
    if not page_raw:
        return old_id, None

    try:
        page = int(page_raw, 10)
        return old_id, page if page >= 1 else None
    except Exception:
        return old_id, None


def _resolve_legacy_redirect(request: Request, source: str) -> "RedirectResponse | None":
    """
    Обрабатывает legacy query-параметр ?id=... и возвращает RedirectResponse.

    - Если параметра id нет — возвращает None (caller продолжает нормальную обработку).
    - Если id присутствует (валидный или нет) — всегда возвращает RedirectResponse
      (на целевой URL или на /?notfound=1).

    Args:
        request: HTTP запрос
        source:  строка для лог-сообщений ("root" / "doc.aspx")
    """
    old_id, page = _parse_legacy_id_page(request)

    if old_id is None:
        return None

    if not old_id.isdigit():
        app_logger.info(f"Legacy redirect ({source}): невалидный id='{old_id}'")
        return RedirectResponse(url="/?notfound=1", status_code=REDIRECT_STATUS_CODE)

    redirect_table = getattr(request.app.state, "redirect_table", None)
    if not isinstance(redirect_table, dict) or not redirect_table:
        app_logger.warning(f"Таблица редиректов не загружена ({source} legacy redirect)")
        return RedirectResponse(url="/?notfound=1", status_code=REDIRECT_STATUS_CODE)

    redirect_target = redirect_table.get(f"id={old_id}")
    if not redirect_target:
        app_logger.info(f"Legacy redirect ({source}): id={old_id} не найден в таблице")
        return RedirectResponse(url="/?notfound=1", status_code=REDIRECT_STATUS_CODE)

    return RedirectResponse(
        url=_build_canonical_redirect_url(str(redirect_target), page),
        status_code=LEGACY_REDIRECT_STATUS_CODE
    )


@router.api_route("/robots.txt", methods=["GET", "HEAD"])
async def robots_txt():
    """Отдаёт robots.txt с актуальным Sitemap и Clean-param из SITE_URL/ROBOTS_CLEAN_PARAMS."""
    base = (SITE_URL or "").rstrip("/")
    content = (
        "User-agent: *\n"
        # PDF открыты для обхода намеренно: краулер должен скачать файл, чтобы
        # прочитать X-Robots-Tag: noindex и убрать «голые» ссылки из индекса.
        # Disallow этого не даёт — он лишь запрещает скачивание, но не удаляет URL.
        "Allow: /api/pdf/\n"
        "Allow: /data/*.pdf$\n"
        "Disallow: /data/\n"
        "Disallow: /data.db/\n"
        "Disallow: /api/\n"
        "Disallow: /health\n"
        f"\nClean-param: {ROBOTS_CLEAN_PARAMS}\n"
        f"\nSitemap: {base}/sitemap.xml\n"
    )
    return Response(content=content, media_type="text/plain")


@router.api_route("/", methods=["GET", "HEAD"])
async def root(request: Request):
    """
    SEO-aware обработчик корня.
    - Legacy ?id= — редирект по таблице (до очистки меток, один переход).
    - Метки из ROBOTS_CLEAN_PARAMS и хвостовой «=» у компактного шифра → 301 без них.
    - Пустой query → кешированный шаблон главной.
    - Компактный URL отчёта (?123, ?123-ТССР) + отчёт найден →
        per-report title/description/canonical + видимый блок маршрута.
    - Фильтры / отчёт не найден → шаблон главной + X-Robots-Tag: noindex, nofollow.
      Для несуществующего отчёта статус 200 (soft-404) — noindex исключает
      попадание в индекс, а честный 404 сломал бы SPA-поведение «ничего не найдено».
    """
    redirect = _resolve_legacy_redirect(request, "root")
    if redirect:
        return redirect

    cleaned = _strip_tracking_query(request.url.query or "")
    if cleaned is not None:
        url = f"/?{cleaned}" if cleaned else "/"
        return RedirectResponse(url=url, status_code=STATIC_REDIRECT_STATUS_CODE)

    raw_query = request.url.query or ""

    if not raw_query:
        return HTMLResponse(content=render_homepage_html(), media_type="text/html")

    parsed = parse_report_query(raw_query)
    if parsed:
        shifr5, dopshifr = parsed
        row = fetch_report_row(shifr5, dopshifr)
        if row:
            return HTMLResponse(content=render_report_html(row), media_type="text/html")

    # Фильтры, soft-404 или нераспознанный query
    response = HTMLResponse(content=render_homepage_html(), media_type="text/html")
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@router.api_route("/index.html", methods=["GET", "HEAD"])
async def index():
    """301 редирект на / — устраняет дубликат главной страницы."""
    return RedirectResponse(url="/", status_code=301)


@router.api_route("/about.html", methods=["GET", "HEAD"])
async def about():
    """Возвращает about.html с canonical + Open Graph (рендер через SEO_HEAD зону)."""
    return HTMLResponse(content=render_about_html(), media_type="text/html")


@router.get("/oldscan.html", response_class=FileResponse)
async def oldscan():
    """Возвращает oldscan.html — страница архивных сканов"""
    return FileResponse("oldscan.html", media_type="text/html")


@router.get("/cloud.html", response_class=FileResponse)
async def cloud():
    """Возвращает cloud.html — страница отчётов в облаке"""
    return FileResponse("cloud.html", media_type="text/html")


@router.get("/login.html", response_class=FileResponse)
async def login():
    """Возвращает login.html — тестовая страница magic link авторизации"""
    return FileResponse("login.html", media_type="text/html")


@router.get("/upload.html", response_class=FileResponse)
async def upload():
    """Возвращает upload.html — страница загрузки отчётов"""
    return FileResponse("upload.html", media_type="text/html")


@router.get("/png-viewer", response_class=FileResponse)
async def png_viewer():
    """Возвращает страницу PNG viewer для embedded-вьюера"""
    return FileResponse("png-viewer.html", media_type="text/html")


@router.get("/favicon.ico", response_class=FileResponse)
async def favicon():
    """Возвращает favicon.ico"""
    favicon_path = Path(FAVICON_PATH)
    if favicon_path.exists():
        return FileResponse(favicon_path, media_type="image/x-icon")
    else:
        # Если favicon нет в assets, пробуем корневую директорию
        if Path("favicon.ico").exists():
            return FileResponse("favicon.ico", media_type="image/x-icon")
        return Response(status_code=404)


# Редиректы с путей без слеша на пути со слешем для каждой статической директории
def _make_static_redirect(path: str):
    """Фабрика: создаёт обработчик редиректа path -> path/."""
    async def _handler():
        return Response(status_code=STATIC_REDIRECT_STATUS_CODE, headers={"Location": f"{path}/"})
    _handler.__name__ = f"redirect_{path.strip('/')}"
    return _handler

for _dir_path in ['/js', '/css', '/assets', LOCAL_ARCHIVE_PATH]:
    router.add_api_route(_dir_path, _make_static_redirect(_dir_path), methods=["GET"])


# Обработчик для редиректа /doc.aspx → / с обработкой таблицы редиректов
@router.get(REDIRECT_SOURCE)
async def redirect_doc_aspx(request: Request):
    """Редиректит с /doc.aspx на / с обработкой таблицы редиректов."""
    redirect = _resolve_legacy_redirect(request, "doc.aspx")
    if redirect:
        return redirect
    # По спецификации legacy URL всегда содержит id. Если нет — показываем notfound.
    return RedirectResponse(url="/?notfound=1", status_code=REDIRECT_STATUS_CODE)


def _make_doc_aspx_alias_handler(alias: str):
    """Фабрика: регистровый алиас /doc.aspx → тот же обработчик."""
    async def _handler(request: Request):
        redirect = _resolve_legacy_redirect(request, alias.lstrip("/"))
        if redirect:
            return redirect
        return RedirectResponse(url="/?notfound=1", status_code=REDIRECT_STATUS_CODE)
    _handler.__name__ = f"redirect_{alias.lstrip('/').replace('.', '_')}"
    return _handler


for _alias in REDIRECT_SOURCE_ALIASES:
    router.add_api_route(_alias, _make_doc_aspx_alias_handler(_alias), methods=["GET"])


def _make_default_aspx_handler(path: str):
    """Фабрика: /default.aspx и варианты регистра → / (или /?id= если есть legacy id)."""
    async def _handler(request: Request):
        redirect = _resolve_legacy_redirect(request, path.lstrip("/"))
        if redirect:
            return redirect
        return RedirectResponse(url="/", status_code=301)
    _handler.__name__ = f"redirect_{path.lstrip('/').replace('.', '_')}"
    return _handler


for _default_path in REDIRECT_DEFAULT_ASPX_PATHS:
    router.add_api_route(_default_path, _make_default_aspx_handler(_default_path), methods=["GET"])


@router.get("/api/redirect-table")
async def get_redirect_table(request: Request):
    """
    Возвращает таблицу редиректов из кэша app.state
    """
    try:
        redirect_data = getattr(request.app.state, 'redirect_table', None)

        if redirect_data is None:
            return JSONResponse({
                "success": False,
                "error": "Таблица редиректов не загружена"
            })

        return JSONResponse({
            "success": True,
            "data": redirect_data
        })

    except Exception as e:
        app_logger.error(f"Ошибка получения таблицы редиректов: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": "Ошибка получения таблицы редиректов"
        })
