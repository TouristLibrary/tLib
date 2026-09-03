"""
API тесты безопасности путей cache_router и png_viewer_router (Этап 4).

Проверяет на реальном стеке (uvicorn + Caddy/Tailscale):
  - body.path traversal в /resolve -> строго 400 {"status":"error","message":"Invalid path"}
  - archive_name backslash -> строго 400
  - URL-traversal -> blocked (400 или 404, никогда не 2xx с реальным файлом)
  - легитимные пути не отвергаются (позитивы; пропускаются если ресурс недоступен на стенде)

Ключевое отличие от integration-тестов: реальный ASGI-стек нормализует URL-пути
ДО роутинга (../ схлопывается, %2F декодируется). Поэтому для URL-traversal ожидаем
"blocked" = 400|404, а конкретный код — предмет наблюдения:
  400 — вектор дошёл до нашего валидатора
  404 — вектор заблокирован ещё на уровне ASGI/роутинга
Оба результата безопасны. FAIL только если вернулся 2xx с реальным файлом.

Запуск:
    cd tests/
    BASE_URL=https://your-server.example.com python -m pytest api/test_cache_png_paths.py -v
"""
from __future__ import annotations

import os

import httpx
import pytest

from _constants import SAMPLE_REPORT

# Статусы успешного resolve (валидный путь не должен возвращать error/400)
_VALID_RESOLVE_STATUSES = {"ready", "preparing", "not_found", "not_prepared"}


@pytest.fixture(scope="module")
def cache_client() -> httpx.Client:
    """httpx.Client с увеличенным таймаутом для cache/png endpoint'ов."""
    base = os.environ.get("BASE_URL", "").rstrip("/")
    with httpx.Client(base_url=base, timeout=60.0, follow_redirects=True) as c:
        yield c


# ---------------------------------------------------------------------------
# Негатив: body.path traversal в POST /api/cache/{name}/resolve
# ---------------------------------------------------------------------------
# body.path приходит как JSON — URL-нормализация ASGI его не затрагивает.
# Валидатор ОБЯЗАН заблокировать -> строго 400.


@pytest.mark.parametrize("bad_path", [
    "../../etc/passwd",
    "..\\..\\Windows\\System32",
    "/etc/passwd",
    "\x00null",
])
def test_resolve_body_path_traversal_returns_400(
    cache_client: httpx.Client,
    bad_path: str,
) -> None:
    """body.path-traversal -> строго 400 {"status":"error","message":"Invalid path"}."""
    resp = cache_client.post(
        f"/api/cache/{SAMPLE_REPORT}/resolve",
        json={"path": bad_path, "kind": "pdf"},
    )
    assert resp.status_code == 400, (
        f"body.path={bad_path!r}: ожидался 400, получен {resp.status_code}: {resp.text[:300]}"
    )
    # Проверяем конверт только при JSON-ответе: прокси может вернуть собственную
    # не-JSON страницу 400 (например на null-byte в теле), и resp.json() бросило бы исключение.
    if "application/json" in resp.headers.get("content-type", ""):
        body = resp.json()
        assert body.get("status") == "error", f"Нет status=error: {body}"
        assert "Invalid path" in body.get("message", ""), f"Нет 'Invalid path' в message: {body}"


# ---------------------------------------------------------------------------
# Негатив: backslash в archive_name
# ---------------------------------------------------------------------------
# Backslash — одиночный URL-сегмент (нет слешей), проходит через роутинг.
# На чистом uvicorn: _validate_archive_name -> 400.
# За прокси (Caddy/Tailscale Funnel) backslash может нормализоваться/отвергаться
# до проксирования -> 404. Оба результата = "заблокировано".
# Строгое доказательство работы валидатора — группа body.path выше (JSON-тело
# не проходит URL-нормализацию ASGI и всегда доходит до хендлера).


def test_archive_name_backslash_is_blocked(cache_client: httpx.Client) -> None:
    """archive_name с backslash заблокирован: 400 (валидатором) или 404 (прокси-слоем)."""
    resp = cache_client.post("/api/cache/foo%5Cbar/prepare")
    assert resp.status_code in (400, 404), (
        f"archive_name backslash не заблокирован: "
        f"ожидался 400 или 404, получен {resp.status_code}: {resp.text[:300]}"
    )


# ---------------------------------------------------------------------------
# Негатив: URL-traversal в path-параметрах
# ---------------------------------------------------------------------------
# Конкретный код: 400 (дошло до валидатора) или 404 (заблокировано ASGI/роутингом).
# FAIL только если вернулся 2xx — значит вектор прошёл и файл/данные утекли.


@pytest.mark.parametrize("url", [
    # double-encoded %2e%2e%2f — на реальном стеке могут декодироваться до роутинга
    f"/api/png/{SAMPLE_REPORT}/%2e%2e%2f/report-png/pages",
    # backslash в dir_path (URL-encoded)
    f"/api/png/{SAMPLE_REPORT}/..%5Creport-png/pages",
    # raw ../  в cache archive_name (URL-encoded слеш)
    "/api/cache/..%2F..%2Fetc/prepare",
    # сырые ../ (нормализуются ASGI, ожидаем 404)
    "/api/png/../../etc/pages",
    "/api/cache/../../etc/prepare",
])
def test_url_traversal_is_blocked(cache_client: httpx.Client, url: str) -> None:
    """
    URL-traversal заблокирован: код 400 (валидатором) или 404 (ASGI/роутингом).
    Критично: не должен вернуться 2xx с реальным файлом.
    """
    resp = cache_client.get(url)
    assert resp.status_code in (400, 404), (
        f"url={url!r}: traversal НЕ заблокирован — "
        f"ожидался 400 или 404, получен {resp.status_code}. "
        f"Тело (300 байт): {resp.text[:300]}"
    )


# ---------------------------------------------------------------------------
# Позитив: валидный body.path не отвергается
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def first_file_from_archive(cache_client: httpx.Client) -> tuple[str, str]:
    """
    Берёт первый файл из /api/cache/{SAMPLE_REPORT}/contents.
    Возвращает (name, kind) — kind берётся из записи contents (fallback "image").
    Пропускает тест если кеш отчёта ещё не прогрет — кеш греется при первом открытии
    карточки отчёта (E2E image-caching прогревает 00001-TST при каждом прогоне).
    """
    probe = cache_client.get(f"/api/cache/{SAMPLE_REPORT}/contents")
    if probe.status_code != 200:
        pytest.skip(
            f"Кеш сида {SAMPLE_REPORT} недоступен (HTTP {probe.status_code}) — "
            "прогрейте кеш, открыв карточку отчёта, или запустите E2E-прогон."
        )
    files = probe.json().get("files", [])
    if not files:
        pytest.skip(
            f"Кеш сида {SAMPLE_REPORT} пуст — прогрейте кеш, открыв карточку отчёта."
        )
    entry = files[0]
    return entry["name"], entry.get("kind", "image")


def test_resolve_valid_path_not_rejected(
    cache_client: httpx.Client,
    first_file_from_archive: tuple[str, str],
) -> None:
    """
    Валидный путь из содержимого кеша -> status не error и не 400.
    Ожидаемые статусы: ready, preparing, not_found, not_prepared.
    """
    path, kind = first_file_from_archive
    resp = cache_client.post(
        f"/api/cache/{SAMPLE_REPORT}/resolve",
        json={"path": path, "kind": kind},
    )
    assert resp.status_code != 400, (
        f"Валидный path={path!r} (kind={kind!r}) отвергнут с 400: {resp.text[:300]}"
    )
    body = resp.json()
    status_val = body.get("status", "")
    assert status_val in _VALID_RESOLVE_STATUSES, (
        f"Неожиданный статус {status_val!r} для валидного пути (kind={kind!r}): {body}"
    )


# ---------------------------------------------------------------------------
# Позитив: /api/png/directories + /pages работают (фикс _list_png_files)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def first_png_directory(cache_client: httpx.Client) -> str:
    """
    Берёт первую PNG-директорию из /api/png/directories.
    Пропускает тест если директорий нет (кеш не прогрет).
    """
    resp = cache_client.get("/api/png/directories")
    if resp.status_code != 200:
        pytest.skip(f"/api/png/directories вернул {resp.status_code}; тест пропущен")
    dirs = resp.json().get("directories", [])
    if not dirs:
        pytest.skip("Нет PNG-директорий в кеше; тест пропущен (прогрейте кеш)")
    return dirs[0]["path"]


def test_png_pages_valid_dir_returns_200(
    cache_client: httpx.Client,
    first_png_directory: str,
) -> None:
    """
    GET /api/png/{valid_dir}/pages == 200 и pages непустой.
    Проверяет, что фикс _list_png_files (relative_to resolved) работает на реальном стеке.
    """
    resp = cache_client.get(f"/api/png/{first_png_directory}/pages")
    assert resp.status_code == 200, (
        f"dir={first_png_directory!r}: ожидался 200, получен {resp.status_code}: {resp.text[:300]}"
    )
    body = resp.json()
    pages = body.get("pages", [])
    assert len(pages) > 0, f"Список страниц пуст для dir={first_png_directory!r}"
    # Проверяем формат URL (нет 500 от relative_to и нет пустых строк)
    for page in pages:
        url = page.get("url", "")
        assert url.startswith("/"), f"URL страницы не начинается с '/': {url!r}"
        assert url.endswith(".png"), f"URL страницы не оканчивается на '.png': {url!r}"
