# Version 1.2 - 21.06.2026 23:35:00 GMT
# Тесты безопасности путей cache_router и png_viewer_router (этап 4)
# Описание: Проверяет, что traverse-векторы в archive_name, body.path и dir_path
#           корректно отклоняются (400), а легитимные пути работают (не 400/500).
#           In-process TestClient; CACHE_DIRECTORY и DATA_DIRECTORY подменяются
#           на tmp-директории через monkeypatch.
# 1.1: усилены ассерты test_resolve_empty_path_not_rejected и
#      test_resolve_valid_nested_path_not_rejected (stub-zip -> авто-триггер prepare -> 200).
# 1.2: тест атрибуции IP/endpoint в _validate_archive_name (spy на security_logger).

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.cache_router as cache_router_module
import routers.png_viewer_router as png_viewer_router_module
import services.cache.cache_service as cache_service_module
from routers.cache_router import router as cache_router
from routers.png_viewer_router import router as png_viewer_router


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_dirs(tmp_path, monkeypatch) -> dict[str, Path]:
    """Создаёт временные директории и патчит CACHE_DIRECTORY / DATA_DIRECTORY."""
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "data.cache"
    data_dir.mkdir()
    cache_dir.mkdir()

    monkeypatch.setattr(cache_router_module, "DATA_DIRECTORY", str(data_dir))
    monkeypatch.setattr(cache_service_module, "CACHE_DIRECTORY", str(cache_dir))
    monkeypatch.setattr(png_viewer_router_module, "CACHE_DIRECTORY", str(cache_dir))

    return {"data": data_dir, "cache": cache_dir}


@pytest.fixture()
def app_client(tmp_dirs) -> TestClient:
    """TestClient с cache_router и png_viewer_router (без lifespan)."""
    application = FastAPI()
    application.include_router(cache_router)
    application.include_router(png_viewer_router)
    with TestClient(application, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _make_archive(data_dir: Path, name: str) -> None:
    """Создаёт фиктивный ZIP-файл архива в data_dir."""
    (data_dir / f"{name}.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)


def _make_png_dir(cache_dir: Path, archive_name: str, rel_png_dir: str) -> Path:
    """Создаёт вложенную PNG-директорию в кэше и кладёт два .png файла."""
    png_dir = cache_dir / archive_name / rel_png_dir
    png_dir.mkdir(parents=True)
    (png_dir / "page_0001.png").write_bytes(b"\x89PNG")
    (png_dir / "page_0002.png").write_bytes(b"\x89PNG")
    return png_dir


# ---------------------------------------------------------------------------
# cache_router: _validate_archive_name traversal
# ---------------------------------------------------------------------------


class TestCacheArchiveNameTraversal:
    """archive_name-traversal во всех трёх endpoint'ах, использующих _validate_archive_name.

    ЗАМЕЧАНИЕ по охвату: `archive_name` — URL path-параметр (одиночный сегмент).
    Starlette нормализует URL-пути ДО роутинга, поэтому векторы с '/' или '..'
    (например ../etc, foo/bar, foo/../bar) обрабатываются на уровне роутинга
    и возвращают 404, не достигая нашего валидатора.
    Здесь тестируем векторы, которые проходят через роутинг и должны быть
    отвергнуты именно нашим _validate_archive_name: backslash.
    """

    @pytest.mark.parametrize("bad_name", [
        "foo\\bar",   # backslash — одиночный сегмент, но перехватывается валидатором
    ])
    def test_prepare_rejects_traversal(self, app_client, bad_name):
        resp = app_client.post(f"/api/cache/{bad_name}/prepare")
        assert resp.status_code == 400, f"ожидался 400 для archive_name={bad_name!r}, получен {resp.status_code}"

    @pytest.mark.parametrize("bad_name", [
        "foo\\bar",
    ])
    def test_contents_rejects_traversal(self, app_client, bad_name):
        resp = app_client.get(f"/api/cache/{bad_name}/contents")
        assert resp.status_code == 400, f"ожидался 400 для archive_name={bad_name!r}, получен {resp.status_code}"

    @pytest.mark.parametrize("bad_name", [
        "foo\\bar",
    ])
    def test_resolve_rejects_traversal(self, app_client, bad_name):
        resp = app_client.post(
            f"/api/cache/{bad_name}/resolve",
            json={"path": "report.pdf", "kind": "pdf"},
        )
        assert resp.status_code == 400, f"ожидался 400 для archive_name={bad_name!r}, получен {resp.status_code}"

    def test_prepare_valid_name_not_400(self, app_client, tmp_dirs):
        """Валидное имя архива не должно отвергаться на этапе валидации (вернёт not_found, не 400)."""
        resp = app_client.post("/api/cache/00001-TST/prepare")
        assert resp.status_code != 400, f"валидное имя отвергнуто: {resp.text}"


# ---------------------------------------------------------------------------
# cache_router: /resolve body.path traversal
# ---------------------------------------------------------------------------


class TestCacheResolveBodyPath:
    """body.path traversal в POST /api/cache/{name}/resolve."""

    @pytest.mark.parametrize("bad_path", [
        "../../etc/passwd",
        "../secret",
        "..\\..\\Windows",
        "/etc/passwd",
        "\x00null",
    ])
    def test_resolve_rejects_traversal_path(self, app_client, tmp_dirs, bad_path):
        _make_archive(tmp_dirs["data"], "00001-TST")
        resp = app_client.post(
            "/api/cache/00001-TST/resolve",
            json={"path": bad_path, "kind": "pdf"},
        )
        assert resp.status_code == 400, (
            f"ожидался 400 для body.path={bad_path!r}, получен {resp.status_code}: {resp.text}"
        )
        data = resp.json()
        assert data.get("status") == "error"
        assert "Invalid path" in data.get("message", "")

    def test_resolve_empty_path_not_rejected(self, app_client, tmp_dirs):
        """Пустой body.path (all_tracks) не должен отвергаться валидатором.

        Stub-zip присутствует -> авто-триггер /resolve запускает prepare в фоне
        и возвращает 200 {"status":"preparing"}.
        """
        _make_archive(tmp_dirs["data"], "00001-TST")
        resp = app_client.post(
            "/api/cache/00001-TST/resolve",
            json={"path": "", "kind": "all_tracks"},
        )
        assert resp.status_code == 200, (
            f"пустой path ошибочно отвергнут (ожидался 200): {resp.status_code} {resp.text}"
        )
        assert resp.json().get("status") != "error", (
            f"пустой path вернул status=error: {resp.json()}"
        )

    def test_resolve_valid_nested_path_not_rejected(self, app_client, tmp_dirs):
        """Валидный вложенный путь не должен отвергаться на этапе валидации пути.

        Stub-zip присутствует -> авто-триггер /resolve запускает prepare в фоне
        и возвращает 200 {"status":"preparing"}.
        """
        _make_archive(tmp_dirs["data"], "00001-TST")
        resp = app_client.post(
            "/api/cache/00001-TST/resolve",
            json={"path": "subdir/report.pdf", "kind": "pdf"},
        )
        assert resp.status_code == 200, (
            f"валидный path отвергнут (ожидался 200): {resp.status_code} {resp.text}"
        )
        assert resp.json().get("status") != "error", (
            f"валидный path вернул status=error: {resp.json()}"
        )


# ---------------------------------------------------------------------------
# png_viewer_router: /pages traversal и корректный ответ
# ---------------------------------------------------------------------------


class TestPngViewerPagesPath:
    """Тесты endpoint'а GET /api/png/{dir_path}/pages.

    ЗАМЕЧАНИЕ по охвату: `dir_path` — URL path-параметр типа :path.
    Starlette нормализует URL-пути ДО роутинга, поэтому явные `../` в начале пути
    (../00001-TST/...) и в середине (00001-TST/../../etc-png) нормализуются на уровне
    роутинга и возвращают 404, не достигая нашего валидатора.
    Наш валидатор перехватывает double-encoded traversal (сохраняется как одиночный
    %2F-сегмент при роутинге) и backslash.
    """

    @pytest.mark.parametrize("bad_path", [
        "00001-TST/%2e%2e%2f/report-png",   # double-encoded, доходит до роутера как один сегмент
        "00001-TST/..\\report-png",          # backslash в сегменте
    ])
    def test_pages_rejects_traversal(self, app_client, bad_path):
        resp = app_client.get(f"/api/png/{bad_path}/pages")
        assert resp.status_code == 400, (
            f"ожидался 400 для dir_path={bad_path!r}, получен {resp.status_code}: {resp.text}"
        )

    def test_pages_rejects_single_segment(self, app_client):
        resp = app_client.get("/api/png/report-png/pages")
        assert resp.status_code == 400

    def test_pages_rejects_non_png_suffix(self, app_client):
        resp = app_client.get("/api/png/00001-TST/report-dir/pages")
        assert resp.status_code == 400

    def test_pages_returns_404_for_nonexistent_dir(self, app_client):
        resp = app_client.get("/api/png/00001-TST/missing-png/pages")
        assert resp.status_code == 404

    def test_pages_valid_dir_returns_200_with_files(self, app_client, tmp_dirs):
        """Легитимная PNG-директория с файлами возвращает 200 без ошибок (нет регрессии 500)."""
        _make_png_dir(tmp_dirs["cache"], "00001-TST", "report-png")
        resp = app_client.get("/api/png/00001-TST/report-png/pages")
        assert resp.status_code == 200, f"ожидался 200, получен {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["total"] == 2
        assert len(data["pages"]) == 2
        # Проверяем, что URL-ы сформированы корректно (нет 500 от relative_to)
        for page in data["pages"]:
            assert page["url"].startswith("/")
            assert page["url"].endswith(".png")

    def test_pages_valid_nested_dir_returns_200(self, app_client, tmp_dirs):
        """Вложенная структура (3+ сегмента) работает корректно."""
        _make_png_dir(tmp_dirs["cache"], "00001-TST", "subdir/report-png")
        resp = app_client.get("/api/png/00001-TST/subdir/report-png/pages")
        assert resp.status_code == 200, f"ожидался 200, получен {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["total"] == 2


# ---------------------------------------------------------------------------
# Атрибуция IP/endpoint в _validate_archive_name (v2.2)
# ---------------------------------------------------------------------------


class TestCacheArchiveNameIpAttribution:
    """Проверяет, что security_logger получает реальный IP и endpoint (а не unknown)."""

    def test_contents_backslash_logs_real_ip_and_endpoint(self, app_client, monkeypatch):
        """
        GET /api/cache/foo%5Cbar/contents (backslash) -> 400.
        security_logger.log_invalid_request должен вызваться с client_ip == 'testclient'
        и endpoint, содержащим '/api/cache/'.
        """
        import services.security.path_validation as pv_module

        calls: list[dict] = []

        def spy(ip: str, endpoint: str, reason: str) -> None:
            calls.append({"ip": ip, "endpoint": endpoint, "reason": reason})

        monkeypatch.setattr(pv_module.security_logger, "log_invalid_request", spy)

        resp = app_client.get("/api/cache/foo%5Cbar/contents")
        assert resp.status_code == 400

        assert calls, "security_logger.log_invalid_request не был вызван"
        call = calls[0]
        assert call["ip"] == "testclient", (
            f"Ожидался ip='testclient', получен {call['ip']!r}. "
            "Убедитесь, что client_ip прокинут в _validate_archive_name."
        )
        assert "/api/cache/" in call["endpoint"], (
            f"endpoint не содержит '/api/cache/': {call['endpoint']!r}"
        )
