# Тесты процесса-воркера рендера PDF→PNG и оптимизации картинок
# (services/conversion/pdf_render_worker.py, пул в services/conversion/pdf_to_png_service.py)
# Описание: Воркер импортирует только stdlib и fitz — config/logging_config в spawn-процессе
#           открыли бы те же app.log/critical.log. render_page пишет PNG атомарно, без tmp-хвостов.
#           optimize_image уменьшает растр или копирует его: PyMuPDF там же, где рендер страниц,
#           иначе стадия картинок держит GIL главного процесса и поиск растягивается в сотни раз.
#           Ошибки задач воркер отдаёт RuntimeError с типом и текстом исходной: исключения MuPDF
#           (битая картинка, страница огромного размера) не проходят pickle.
#           Умерший воркер (BrokenProcessPool) сбрасывает пул: рендер пробрасывает ошибку,
#           оптимизация картинки возвращает False (вызывающий кладёт в кеш оригинал).
#           Сброс не трогает уже пересозданный пул. Остановка в lifespan ждёт выхода воркера
#           (wait=True): uvicorn умирает без atexit. PDF и картинки генерирует PyMuPDF;
#           без fitz тесты пропускаются.

import json
import pickle
import subprocess
import sys
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

import services.conversion.image_conversion_service as image_service
import services.conversion.pdf_to_png_service as pdf_service
from services.conversion import pdf_render_worker
from services.conversion.pdf_to_png_service import ConversionConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Минимальный DPI — тесты проверяют запись файла, а не качество
_CONFIG = ConversionConfig(dpi=10, zoom=10 / 72.0, colorspace="gray", alpha=False)


def _make_pdf(path, pages=2):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=200, height=200)
        page.insert_text((20, 100), f"page {i + 1}")
    doc.save(str(path))
    doc.close()


class _FakePool:
    """Пул, чей воркер умер: каждая задача падает BrokenProcessPool."""

    def __init__(self):
        self.shutdown_calls = []

    def submit(self, fn, *args):
        future = Future()
        future.set_exception(BrokenProcessPool("A process in the process pool was terminated abruptly"))
        return future

    def shutdown(self, wait=True, cancel_futures=False):
        self.shutdown_calls.append(wait)


def test_worker_imports_no_project_modules():
    """Чистый интерпретатор, как spawn-ребёнок: из модулей проекта грузятся только пакеты
    по пути к воркеру, без config и logging_config."""
    code = (
        "import json, sys\n"
        "import services.conversion.pdf_render_worker\n"
        "tops = {'config', 'logging_config', 'services', 'routers', 'middlewares', 'app'}\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] in tops)))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT,
                            capture_output=True, text=True, timeout=120)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "services", "services.conversion", "services.conversion.pdf_render_worker",
    ]


def test_render_page_writes_png_without_tmp(tmp_path):
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf)
    out = tmp_path / "doc_0002.png"

    assert pdf_render_worker.page_count(str(pdf)) == 2
    pdf_render_worker.render_page(str(pdf), 1, str(out), _CONFIG.zoom, "gray", False)

    assert out.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert fitz.Pixmap(str(out)).n == 1, "colorspace gray должен дать один канал"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["doc.pdf", "doc_0002.png"]


def test_broken_pool_is_reset_and_reraised(tmp_path, monkeypatch):
    """Воркер умер — пул сброшен (следующий рендер создаст новый), исключение пробрасывается:
    convert_pdf_to_directory превращает его в ошибку запуска, как исключение MuPDF."""
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf)
    pool = _FakePool()
    monkeypatch.setattr(pdf_service, "_render_pool", pool)

    with pytest.raises(BrokenProcessPool):
        pdf_service._convert_pdf_to_directory_sync(pdf, tmp_path / "doc-png", "doc", _CONFIG)

    assert pdf_service._render_pool is None
    assert pool.shutdown_calls == [False]


def test_reset_keeps_newer_pool(monkeypatch):
    """Второй поток получил BrokenProcessPool старого пула, когда третий уже создал новый:
    сброс закрывает только старый."""
    old, new = _FakePool(), _FakePool()
    monkeypatch.setattr(pdf_service, "_render_pool", new)

    pdf_service.reset_render_pool(old)

    assert pdf_service._render_pool is new
    assert (old.shutdown_calls, new.shutdown_calls) == ([False], [])


def test_shutdown_waits_for_worker(monkeypatch):
    """Lifespan shutdown ждёт выхода воркера: после lifespan uvicorn переподнимает SIGTERM
    и умирает без atexit, и воркер с недошедшим sentinel жил бы до SIGKILL. Без пула — no-op."""
    pool = _FakePool()
    monkeypatch.setattr(pdf_service, "_render_pool", pool)

    pdf_service.shutdown_render_pool()
    pdf_service.shutdown_render_pool()

    assert pdf_service._render_pool is None
    assert pool.shutdown_calls == [True]


def _make_image(path, width, height):
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), 0)
    pix.save(str(path), "png" if path.suffix.lower() == ".png" else "jpeg")


class _InlinePool:
    """Пул, выполняющий задачу в этом же процессе: проверяет связку сервиса и воркера без spawn."""

    def submit(self, fn, *args):
        future = Future()
        future.set_result(fn(*args))
        return future


def test_optimize_image_resizes_jpeg_keeps_png_copies_small(tmp_path):
    """Крупный JPEG уменьшается до лимитов, PNG остаётся PNG, картинка в лимитах копируется байт в байт."""
    big = tmp_path / "big.jpg"
    _make_image(big, 2000, 1500)
    jpeg_out = tmp_path / "big-out.jpg"
    src_w, src_h, dst_w, dst_h, copied = pdf_render_worker.optimize_image(
        str(big), str(jpeg_out), 1280, 960, 85, False)
    assert (src_w, src_h, copied) == (2000, 1500, False)
    assert dst_w <= 1280 and dst_h <= 960
    assert jpeg_out.read_bytes().startswith(b"\xff\xd8")
    out = fitz.Pixmap(str(jpeg_out))
    assert out.width == dst_w and out.height <= 960

    png = tmp_path / "big.png"
    _make_image(png, 2000, 1500)
    png_out = tmp_path / "big-out.png"
    pdf_render_worker.optimize_image(str(png), str(png_out), 1280, 960, 85, True)
    assert png_out.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

    small = tmp_path / "small.jpg"
    _make_image(small, 100, 80)
    small_out = tmp_path / "small-out.jpg"
    result = pdf_render_worker.optimize_image(str(small), str(small_out), 1280, 960, 85, False)
    assert result == (100, 80, 100, 80, True)
    assert small_out.read_bytes() == small.read_bytes()


def test_optimize_image_error_survives_pickle(tmp_path):
    """Ошибка MuPDF на битой картинке не проходит pickle между процессами: воркер отдаёт
    RuntimeError с её текстом, иначе в critical.log вместо причины было бы «cannot pickle…»."""
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")

    with pytest.raises(RuntimeError) as excinfo:
        pdf_render_worker.optimize_image(str(bad), str(tmp_path / "out.jpg"), 1280, 960, 85, False)

    restored = pickle.loads(pickle.dumps(excinfo.value))
    assert "unknown image file format" in str(restored)


def test_render_page_error_survives_pickle(tmp_path):
    """То же для страницы PDF: пиксмап, который MuPDF не может выделить (страница огромного
    размера), даёт FzErrorSystem. Запрос на 844 ТБ отказывает сразу — память не выделяется."""
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, pages=1)

    with pytest.raises(RuntimeError) as excinfo:
        pdf_render_worker.render_page(str(pdf), 0, str(tmp_path / "out.png"), 1e6, "gray", False)

    restored = pickle.loads(pickle.dumps(excinfo.value))
    assert "FzErrorSystem" in str(restored) and "malloc" in str(restored)
    assert not (tmp_path / "out.png").exists()


def test_page_count_error_names_original_type(tmp_path):
    """Ошибки самого PyMuPDF pickle проходят, но идут тем же путём: тип исходной — в тексте."""
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")

    with pytest.raises(RuntimeError, match="FileDataError"):
        pdf_render_worker.page_count(str(bad))


def test_optimize_image_sync_runs_in_pool(tmp_path, monkeypatch):
    """Сервис ставит задачу в пул и по результату воркера пишет готовый файл."""
    src = tmp_path / "big.jpg"
    dst = tmp_path / "out" / "big.jpg"
    _make_image(src, 2000, 1500)
    monkeypatch.setattr(image_service, "get_render_pool", lambda: _InlinePool())

    assert image_service.optimize_image_sync(src, dst, 1280, 960) is True

    out = fitz.Pixmap(str(dst))
    assert out.width <= 1280 and out.height <= 960


def test_optimize_image_sync_broken_pool_returns_false(tmp_path, monkeypatch):
    """Умерший воркер — False, как ошибка оптимизации: вызывающий кладёт в кеш оригинал.
    Пул сброшен, следующий рендер создаст новый."""
    pool = _FakePool()
    monkeypatch.setattr(pdf_service, "_render_pool", pool)
    src = tmp_path / "a.jpg"
    src.write_bytes(b"not-an-image")

    assert image_service.optimize_image_sync(src, tmp_path / "out.jpg", 1280, 960) is False

    assert pdf_service._render_pool is None
    assert pool.shutdown_calls == [False]
    assert not (tmp_path / "out.jpg").exists()
