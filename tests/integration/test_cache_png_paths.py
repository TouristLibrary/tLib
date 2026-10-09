# Version 2.3 - 09.10.2026 10:20:00 GMT
# Тесты безопасности путей cache_router и png_viewer_router (этап 4)
# Описание: Проверяет, что traverse-векторы в archive_name, body.path и dir_path
#           корректно отклоняются (400), а легитимные пути работают (не 400/500).
#           In-process TestClient; CACHE_DIRECTORY и DATA_DIRECTORY подменяются
#           на tmp-директории через monkeypatch.
# 1.1: усилены ассерты test_resolve_empty_path_not_rejected и
#      test_resolve_valid_nested_path_not_rejected (stub-zip -> авто-триггер prepare -> 200).
# 1.2: тест атрибуции IP/endpoint в _validate_archive_name (spy на security_logger).
# 1.3: /prepare?probe=1 — проба не запускает подготовку кеша (TestCachePrepareProbe).
# 1.4: kind=pdf убран из /resolve — traversal-проверки переведены на kind=image,
#      добавлен TestCacheResolveKind (pdf и неизвестный kind -> 400 без запуска подготовки).
# 1.5: TestConvertWhileWatching — /pages?want= пишет _watch.json, partial-директория
#      запускает resume_pdf_conversion, /prepare?probe=1 не отдаёт pages для partial.
# 1.6: /pages обновляет mtime папки архива (LRU-метка просмотра PDF).
# 1.7: /prepare при валидном кеше больше не докручивает PDF на паузе — триггер докрутки
#      только /pages (spy resume_pdf_conversion — только в png_viewer_router).
# 1.8: /prepare?probe=1 отдаёт pages и для PDF на паузе — вьюер открывается без жеста.
# 1.9: /pages пишет «PDF page wait», только пока страницы want нет на диске.
# 2.0: long-poll /pages — ответ, как только PNG want появился, или по PNG_PAGES_WAIT_SECONDS;
#      «PDF page wait» с waited_ms и ready; готовая директория и готовая want — без ожидания.
# 2.1: test_pages_on_partial_dir_resumes_conversion без long-poll (WAIT=0) — проверяет докрутку, не ожидание.
# 2.2: поле rendering в /pages — идёт подготовка архива или нужна докрутка по гистерезису (needs_resume);
# 2.3: rendering=false и докрутка не ставится, если исходника нет, запись не partial или нет meta.
#      готовая директория и директория без маркера — false.

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routers.cache_router as cache_router_module
import routers.png_viewer_router as png_viewer_router_module
import services.cache.cache_service as cache_service_module
import services.cache.cache_watch as cache_watch_module
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
            json={"path": "photo.jpg", "kind": "image"},
        )
        assert resp.status_code == 400, f"ожидался 400 для archive_name={bad_name!r}, получен {resp.status_code}"

    def test_prepare_valid_name_not_400(self, app_client, tmp_dirs):
        """Валидное имя архива не должно отвергаться на этапе валидации (вернёт not_found, не 400)."""
        resp = app_client.post("/api/cache/00001-TST/prepare")
        assert resp.status_code != 400, f"валидное имя отвергнуто: {resp.text}"


# ---------------------------------------------------------------------------
# cache_router: /prepare?probe=1 не запускает подготовку
# ---------------------------------------------------------------------------


class TestCachePrepareProbe:
    """Проба нужна для рендера карточки: headless-боты не должны запускать конвертацию.

    TestClient выполняет BackgroundTasks синхронно после ответа, поэтому
    отсутствие директории кеша после запроса доказывает, что задача не ставилась.
    """

    def test_probe_zip_returns_not_prepared_without_starting(self, app_client, tmp_dirs):
        _make_archive(tmp_dirs["data"], "00001-TST")
        resp = app_client.post("/api/cache/00001-TST/prepare?probe=1")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "not_prepared"
        assert data["files"] == []
        assert not (tmp_dirs["cache"] / "00001-TST").exists(), "проба запустила подготовку кеша"

    def test_probe_standalone_pdf_returns_not_prepared_without_starting(self, app_client, tmp_dirs):
        (tmp_dirs["data"] / "00002-TST.pdf").write_bytes(b"%PDF-1.4\n")
        resp = app_client.post("/api/cache/00002-TST/prepare?probe=1")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"status": "not_prepared"}
        assert not (tmp_dirs["cache"] / "00002-TST").exists(), "проба запустила конвертацию PDF"

    def test_without_probe_still_starts(self, app_client, tmp_dirs):
        """Без probe поведение прежнее — старый JS в кэше браузеров продолжает работать."""
        _make_archive(tmp_dirs["data"], "00001-TST")
        resp = app_client.post("/api/cache/00001-TST/prepare")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "started"

    def test_probe_missing_archive_returns_not_found(self, app_client, tmp_dirs):
        resp = app_client.post("/api/cache/00003-TST/prepare?probe=1")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "not_found"


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
            json={"path": bad_path, "kind": "image"},
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
            json={"path": "subdir/photo.jpg", "kind": "image"},
        )
        assert resp.status_code == 200, (
            f"валидный path отвергнут (ожидался 200): {resp.status_code} {resp.text}"
        )
        assert resp.json().get("status") != "error", (
            f"валидный path вернул status=error: {resp.json()}"
        )


class TestCacheResolveKind:
    """kind=pdf убран из /resolve: PDF-вьюер ходит в /api/png/.../pages напрямую.

    Неизвестный kind не должен проваливаться в авто-запуск подготовки (шаг 4):
    TestClient выполняет BackgroundTasks синхронно, поэтому отсутствие директории
    кеша после запроса доказывает, что подготовка не запускалась.
    """

    @pytest.mark.parametrize("kind", ["pdf", "bogus"])
    def test_resolve_rejects_unsupported_kind(self, app_client, tmp_dirs, kind):
        _make_archive(tmp_dirs["data"], "00001-TST")
        (tmp_dirs["data"] / "00001-TST.pdf").write_bytes(b"%PDF-1.4\n")
        resp = app_client.post(
            "/api/cache/00001-TST/resolve",
            json={"path": "report.pdf", "kind": kind},
        )
        assert resp.status_code == 400, resp.text
        assert resp.json() == {"status": "error", "message": "Invalid kind"}
        assert not (tmp_dirs["cache"] / "00001-TST").exists(), "resolve запустил подготовку кеша"


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
# Конвертация PDF, пока смотрят: heartbeat, partial, докрутка
# ---------------------------------------------------------------------------


def _make_partial_cache(data_dir: Path, cache_dir: Path, archive_name: str) -> Path:
    """Stub-кеш ZIP с PDF на паузе: meta (status=partial), _pages_total.txt=3 и одна PNG.

    source в meta совпадает со stub-zip по mtime/size — кеш валиден.
    """
    _make_archive(data_dir, archive_name)
    zip_path = data_dir / f"{archive_name}.zip"
    stat = zip_path.stat()
    png_dir = cache_dir / archive_name / "dir1" / "report-png"
    png_dir.mkdir(parents=True)
    (png_dir / "_pages_total.txt").write_text("3")
    (png_dir / "report_0001.png").write_bytes(b"\x89PNG")
    meta = {
        "version": 1,
        "source": {"path": str(zip_path), "mtime": stat.st_mtime, "size": stat.st_size},
        "cache_size_bytes": 4,
        "files": [{
            "zip_path": "dir1/report.pdf", "kind": "pdf", "size": 1000,
            "png_dir": "dir1/report-png", "pages": 3,
            "status": "partial", "pages_done": 1,
        }],
    }
    (cache_dir / archive_name / "_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return png_dir


@pytest.fixture()
def resume_calls(monkeypatch) -> list[tuple]:
    """Spy на resume_pdf_conversion в png_viewer_router — единственном триггере докрутки.

    Роутер ставит докрутку через create_task и отдаёт ей управление sleep(0) — вызов
    фиксируется до ответа.
    """
    calls: list[tuple] = []

    async def spy(archive_name, png_dir_rel):
        calls.append((archive_name, png_dir_rel))

    monkeypatch.setattr(png_viewer_router_module, "resume_pdf_conversion", spy)
    return calls


def _page_waits(caplog) -> list[dict]:
    """Поля строк «PDF page wait» — по одной на запрос, в котором читатель ждал страницу."""
    return [r.extra_data for r in caplog.records if r.getMessage() == "PDF page wait"]


class TestConvertWhileWatching:
    """png-viewer опрашивает /pages — это heartbeat; конвертация идёт, только пока смотрят."""

    def test_pages_want_writes_watch_heartbeat(self, app_client, tmp_dirs):
        png_dir = _make_png_dir(tmp_dirs["cache"], "00001-TST", "report-png")
        resp = app_client.get("/api/png/00001-TST/report-png/pages?want=3")
        assert resp.status_code == 200, resp.text
        watch = json.loads((png_dir / "_watch.json").read_text(encoding="utf-8"))
        assert watch["want"] == 3
        assert watch["ts"] > 0

    def test_pages_touches_archive_dir_for_lru(self, app_client, tmp_dirs):
        """LRU вытесняет папки архивов по mtime; PDF-вьюер в /resolve не ходит — метку обновляет /pages."""
        _make_png_dir(tmp_dirs["cache"], "00001-TST", "report-png")
        archive_dir = tmp_dirs["cache"] / "00001-TST"
        old_mtime = 1_000_000_000
        os.utime(archive_dir, (old_mtime, old_mtime))
        resp = app_client.get("/api/png/00001-TST/report-png/pages")
        assert resp.status_code == 200, resp.text
        assert archive_dir.stat().st_mtime > old_mtime + 1

    def test_pages_without_want_writes_null_want(self, app_client, tmp_dirs):
        png_dir = _make_png_dir(tmp_dirs["cache"], "00001-TST", "report-png")
        resp = app_client.get("/api/png/00001-TST/report-png/pages")
        assert resp.status_code == 200, resp.text
        assert json.loads((png_dir / "_watch.json").read_text(encoding="utf-8"))["want"] is None

    def test_pages_on_partial_dir_resumes_conversion(self, app_client, tmp_dirs, resume_calls, monkeypatch):
        """Докрутка ставится; long-poll здесь не проверяется — заглушка так и не появится."""
        monkeypatch.setattr(png_viewer_router_module, "PNG_PAGES_WAIT_SECONDS", 0)
        _make_partial_cache(tmp_dirs["data"], tmp_dirs["cache"], "00001-TST")
        resp = app_client.get("/api/png/00001-TST/dir1/report-png/pages?want=2")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert (data["total"], data["pages_total"]) == (1, 3)
        assert resume_calls == [("00001-TST", "dir1/report-png")]

    def test_pages_on_complete_dir_does_not_resume(self, app_client, tmp_dirs, resume_calls):
        png_dir = _make_png_dir(tmp_dirs["cache"], "00001-TST", "report-png")
        (png_dir / "_pages_total.txt").write_text("2")
        resp = app_client.get("/api/png/00001-TST/report-png/pages")
        assert resp.status_code == 200, resp.text
        assert resume_calls == []

    def test_pages_while_preparing_does_not_resume(self, app_client, tmp_dirs, resume_calls):
        """Идёт подготовка архива — она сама конвертирует PDF, докрутка не запускается."""
        _make_partial_cache(tmp_dirs["data"], tmp_dirs["cache"], "00001-TST")
        prepare = {"status": "preparing", "stage": "converting",
                   "updated_at": datetime.now(timezone.utc).isoformat()}
        (tmp_dirs["cache"] / "00001-TST" / "_prepare.json").write_text(json.dumps(prepare), encoding="utf-8")
        resp = app_client.get("/api/png/00001-TST/dir1/report-png/pages")
        assert resp.status_code == 200, resp.text
        assert resume_calls == []

    def test_pages_logs_page_wait_while_want_missing(self, app_client, tmp_dirs, resume_calls, caplog,
                                                     monkeypatch):
        """«PDF page wait» — читатель видит заглушку want; converting — PDF, который рендерится сейчас.
        Страница готова — строки нет. Без long-poll (WAIT=0) ответ сразу, ready=False."""
        monkeypatch.setattr(png_viewer_router_module, "PNG_PAGES_WAIT_SECONDS", 0)
        png_dir = _make_partial_cache(tmp_dirs["data"], tmp_dirs["cache"], "00001-TST")
        prepare = {"status": "preparing", "stage": "converting", "converting_path": "dir1/other.pdf",
                   "updated_at": datetime.now(timezone.utc).isoformat()}
        (tmp_dirs["cache"] / "00001-TST" / "_prepare.json").write_text(json.dumps(prepare), encoding="utf-8")
        caplog.set_level(logging.INFO, logger="tlibwebapp")

        resp = app_client.get("/api/png/00001-TST/dir1/report-png/pages?want=2")
        assert resp.status_code == 200, resp.text
        (wait,) = _page_waits(caplog)
        assert wait.pop("waited_ms") < 100
        assert wait == {"archive": "00001-TST", "png_dir": "dir1/report-png", "want": 2,
                        "done": 1, "total": 3, "converting": "dir1/other.pdf", "ready": False}

        caplog.clear()
        (png_dir / "report_0002.png").write_bytes(b"\x89PNG")
        resp = app_client.get("/api/png/00001-TST/dir1/report-png/pages?want=2")
        assert resp.status_code == 200, resp.text
        assert _page_waits(caplog) == []

    def test_pages_waits_for_want_page(self, app_client, tmp_dirs, resume_calls, caplog, monkeypatch):
        """Long-poll: PNG want появляется во время запроса — ответ сразу с ним, не по таймауту."""
        monkeypatch.setattr(png_viewer_router_module, "PNG_PAGES_WAIT_SECONDS", 5.0)
        png_dir = _make_partial_cache(tmp_dirs["data"], tmp_dirs["cache"], "00001-TST")
        caplog.set_level(logging.INFO, logger="tlibwebapp")
        render = threading.Timer(0.2, (png_dir / "report_0002.png").write_bytes, args=(b"\x89PNG",))

        started = time.perf_counter()
        render.start()
        try:
            resp = app_client.get("/api/png/00001-TST/dir1/report-png/pages?want=2")
        finally:
            render.cancel()
        elapsed = time.perf_counter() - started

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert [p["name"] for p in data["pages"]] == ["report_0001.png", "report_0002.png"]
        assert 0.2 <= elapsed < 5.0
        assert resume_calls == [("00001-TST", "dir1/report-png")]
        (wait,) = _page_waits(caplog)
        assert (wait["ready"], wait["done"]) == (True, 2)
        assert 0 < wait["waited_ms"] < 5000

    def test_pages_wait_times_out(self, app_client, tmp_dirs, resume_calls, caplog, monkeypatch):
        """Страница за PNG_PAGES_WAIT_SECONDS не появилась — ответ без неё, ready=False."""
        monkeypatch.setattr(png_viewer_router_module, "PNG_PAGES_WAIT_SECONDS", 0.3)
        _make_partial_cache(tmp_dirs["data"], tmp_dirs["cache"], "00001-TST")
        caplog.set_level(logging.INFO, logger="tlibwebapp")

        started = time.perf_counter()
        resp = app_client.get("/api/png/00001-TST/dir1/report-png/pages?want=2")
        elapsed = time.perf_counter() - started

        assert resp.status_code == 200, resp.text
        assert resp.json()["total"] == 1
        assert 0.3 <= elapsed < 1.0
        (wait,) = _page_waits(caplog)
        assert wait["ready"] is False
        assert wait["waited_ms"] >= 300

    @pytest.mark.parametrize("case", ["complete_dir", "want_ready"])
    def test_pages_complete_dir_no_wait(self, app_client, tmp_dirs, resume_calls, caplog, monkeypatch, case):
        """Готовая директория или готовая страница want — ответ без ожидания, быстрее шага."""
        monkeypatch.setattr(png_viewer_router_module, "PNG_PAGES_WAIT_SECONDS", 5.0)
        monkeypatch.setattr(png_viewer_router_module, "PNG_PAGES_WAIT_STEP_SECONDS", 1.0)
        if case == "complete_dir":
            png_dir = _make_png_dir(tmp_dirs["cache"], "00001-TST", "report-png")
            (png_dir / "_pages_total.txt").write_text("2")
            url = "/api/png/00001-TST/report-png/pages?want=2"
        else:
            _make_partial_cache(tmp_dirs["data"], tmp_dirs["cache"], "00001-TST")
            url = "/api/png/00001-TST/dir1/report-png/pages?want=1"
        caplog.set_level(logging.INFO, logger="tlibwebapp")

        started = time.perf_counter()
        resp = app_client.get(url)
        elapsed = time.perf_counter() - started

        assert resp.status_code == 200, resp.text
        assert elapsed < 1.0
        assert _page_waits(caplog) == []

    @pytest.mark.parametrize("want, extra_pages, preparing, expected", [
        (2, (), False, True),     # страницы want нет — докрутка нужна
        (1, (), False, False),    # половина окна от want готова, позади дырок нет — рисовать нечего
        (1, (), True, True),      # идёт подготовка архива — страницы ещё появятся
        (3, (3,), False, True),   # want готова, позади дырка (стр. 2) — дозаполнение
    ], ids=["want_missing", "window_ready", "preparing", "hole_behind"])
    def test_pages_rendering_follows_resume_rule(self, app_client, tmp_dirs, resume_calls, monkeypatch,
                                                 want, extra_pages, preparing, expected):
        """rendering — сервер рисует для want: вьюер опрашивает /pages, только пока он true, иначе
        молчит до смены страницы. Правило — гистерезис докрутки (needs_resume): половина окна от want
        и дырки позади. Окно N = 2 — половина окна в одну страницу."""
        monkeypatch.setattr(png_viewer_router_module, "PNG_PAGES_WAIT_SECONDS", 0)
        monkeypatch.setattr(cache_watch_module, "PDF_CONVERT_LOOKAHEAD_PAGES", 2)
        png_dir = _make_partial_cache(tmp_dirs["data"], tmp_dirs["cache"], "00001-TST")
        for page in extra_pages:
            (png_dir / f"report_{page:04d}.png").write_bytes(b"\x89PNG")
        if preparing:
            prepare = {"status": "preparing", "stage": "converting",
                       "updated_at": datetime.now(timezone.utc).isoformat()}
            (tmp_dirs["cache"] / "00001-TST" / "_prepare.json").write_text(json.dumps(prepare), encoding="utf-8")

        resp = app_client.get(f"/api/png/00001-TST/dir1/report-png/pages?want={want}")

        assert resp.status_code == 200, resp.text
        assert resp.json()["rendering"] is expected

    @pytest.mark.parametrize("case", ["source_missing", "entry_not_partial", "no_meta"])
    def test_pages_rendering_false_when_resume_cannot_start(self, app_client, tmp_dirs, resume_calls,
                                                            monkeypatch, case):
        """Докрутка заведомо не стартует — вьюер не опрашивает: rendering=false, задача не ставится.
        Страницы want нет, раньше needs_resume отвечал бы true."""
        monkeypatch.setattr(png_viewer_router_module, "PNG_PAGES_WAIT_SECONDS", 0)
        _make_partial_cache(tmp_dirs["data"], tmp_dirs["cache"], "00001-TST")
        meta_path = tmp_dirs["cache"] / "00001-TST" / "_meta.json"
        if case == "source_missing":
            (tmp_dirs["data"] / "00001-TST.zip").unlink()
        elif case == "entry_not_partial":
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["files"][0]["status"] = "complete"
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
        else:
            meta_path.unlink()

        resp = app_client.get("/api/png/00001-TST/dir1/report-png/pages?want=2")

        assert resp.status_code == 200, resp.text
        assert resp.json()["rendering"] is False
        assert resume_calls == []

    def test_pages_rendering_false_without_missing_pages(self, app_client, tmp_dirs):
        """Готовая директория и директория без маркера _pages_total.txt — рисовать нечего."""
        png_dir = _make_png_dir(tmp_dirs["cache"], "00001-TST", "report-png")
        resp = app_client.get("/api/png/00001-TST/report-png/pages?want=1")
        assert resp.status_code == 200, resp.text
        assert resp.json()["rendering"] is False

        (png_dir / "_pages_total.txt").write_text("2")
        resp = app_client.get("/api/png/00001-TST/report-png/pages?want=1")
        assert resp.status_code == 200, resp.text
        assert resp.json()["rendering"] is False

    def test_probe_returns_pages_of_partial_pdf(self, app_client, tmp_dirs):
        """PDF на паузе отдаётся с полным числом страниц: вьюер открывается сразу, без жеста,
        показывает готовые страницы и докручивает остальные через опрос /pages."""
        _make_partial_cache(tmp_dirs["data"], tmp_dirs["cache"], "00001-TST")
        resp = app_client.post("/api/cache/00001-TST/prepare?probe=1")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "ready"
        pdf = data["files"][0]
        assert pdf["pages"] == 3
        assert pdf["png_dir"] == "00001-TST/dir1/report-png"

    def test_prepare_does_not_resume_partial_pdf(self, app_client, tmp_dirs):
        """/prepare при валидном кеше не докручивает PDF на паузе: без зрителя окно рендера —
        первые страницы, докрутка была бы холостым lock + перераспаковкой.

        Докрутка stub-кеша (пустой ZIP) упала бы и перевела запись в error — запись остаётся partial.
        """
        _make_partial_cache(tmp_dirs["data"], tmp_dirs["cache"], "00001-TST")
        resp = app_client.post("/api/cache/00001-TST/prepare")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "ready"
        meta = json.loads((tmp_dirs["cache"] / "00001-TST" / "_meta.json").read_text(encoding="utf-8"))
        assert (meta["files"][0]["status"], meta["files"][0]["pages_done"]) == ("partial", 1)
        assert not (tmp_dirs["cache"] / "00001-TST" / "_prepare.lockdir").exists()


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
