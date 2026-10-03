# Version 1.1 - 03.10.2026 10:17:22 GMT
# Тесты процесса-воркера рендера PDF→PNG (services/conversion/pdf_render_worker.py,
# пул в services/conversion/pdf_to_png_service.py)
# Описание: Воркер импортирует только stdlib и fitz — config/logging_config в spawn-процессе
#           открыли бы те же app.log/critical.log. render_page пишет PNG атомарно, без tmp-хвостов.
#           Умерший воркер (BrokenProcessPool) сбрасывает пул и пробрасывается: запуск завершается
#           ошибкой, следующий рендер создаёт новый пул; сброс не трогает уже пересозданный пул.
#           Остановка в lifespan ждёт выхода воркера (wait=True): uvicorn умирает без atexit.
#           PDF генерируется PyMuPDF; без fitz тесты пропускаются.
# 1.1: test_shutdown_waits_for_worker.

import json
import subprocess
import sys
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

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

    pdf_service._reset_pool(old)

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
