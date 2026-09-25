# Version 1.1 - 25.09.2026 15:00:00 GMT
# Тесты конвертации PDF, пока смотрят (services/conversion/pdf_to_png_service.py,
# services/cache/cache_prepare_service.resume_pdf_conversion)
# Описание: Конвертер без heartbeat встаёт на паузу, докрутка рендерит только недостающие
#           страницы (готовые PNG не перерисовываются), запрошенная страница рендерится первой.
#           Сквозные сценарии: standalone PDF и PDF из ZIP уходят в partial и докручиваются;
#           сбой докрутки переводит запись в error и освобождает lock.
#           PDF генерируется PyMuPDF; без fitz тесты пропускаются.
# 1.1: порядок рендеринга — по снимкам готовых PNG перед каждой страницей (без mtime);
#      сбой докрутки закрывает директорию на готовых страницах, повторная докрутка — no-op.

import asyncio
import json
import time
import zipfile

import pytest

fitz = pytest.importorskip("fitz")

import services.cache.cache_prepare_service as prepare_service
import services.cache.cache_service as cache_service_module
import services.conversion.pdf_to_png_service as pdf_service
from services.cache.cache_watch import is_partial
from services.cache.cache_prepare_service import (
    convert_standalone_pdf,
    prepare_archive_cache,
    resume_pdf_conversion,
)
from services.conversion.pdf_to_png_service import (
    ConversionConfig,
    _convert_pdf_to_directory_sync,
    generate_png_filename,
)

PAGES = 6
STEM = "doc"

# Минимальный DPI — тесты проверяют порядок и пропуск страниц, а не качество
_CONFIG = ConversionConfig(dpi=10, zoom=10 / 72.0, colorspace="gray", alpha=False)


def _run(coro):
    """Выполняет корутину в собственном loop: asyncio.run() обнулил бы текущий loop,
    на который полагаются другие тесты (asyncio.get_event_loop())."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_pdf(path, pages=PAGES):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=200, height=200)
        page.insert_text((20, 100), f"page {i + 1}")
    doc.save(str(path))
    doc.close()


def _png(out_dir, page_num):
    """Путь к PNG страницы (1-based)."""
    return out_dir / generate_png_filename(STEM, page_num - 1)


@pytest.fixture()
def pdf_path(tmp_path):
    path = tmp_path / f"{STEM}.pdf"
    _make_pdf(path)
    return path


@pytest.fixture()
def no_idle(monkeypatch):
    """Окно тишины меньше нуля: запуск без heartbeat рендерит ровно одну страницу."""
    monkeypatch.setattr(pdf_service, "PDF_CONVERT_IDLE_TIMEOUT_SECONDS", -1)


# ---------------------------------------------------------------------------
# Конвертер: пауза, докрутка, порядок
# ---------------------------------------------------------------------------


def test_pauses_after_first_page_without_heartbeat(tmp_path, pdf_path, no_idle):
    out_dir = tmp_path / f"{STEM}-png"

    pages_done, page_count, _size, completed = _convert_pdf_to_directory_sync(
        pdf_path, out_dir, STEM, _CONFIG
    )

    assert (pages_done, page_count, completed) == (1, PAGES, False)
    assert sorted(p.name for p in out_dir.glob("*.png")) == [_png(out_dir, 1).name]


def test_resume_renders_only_missing_pages(tmp_path, pdf_path):
    out_dir = tmp_path / f"{STEM}-png"
    _convert_pdf_to_directory_sync(pdf_path, out_dir, STEM, _CONFIG)
    for n in (4, 5, 6):
        _png(out_dir, n).unlink()
    mtimes_before = {n: _png(out_dir, n).stat().st_mtime_ns for n in (1, 2, 3)}

    progress = []
    pages_done, page_count, _size, completed = _convert_pdf_to_directory_sync(
        pdf_path, out_dir, STEM, _CONFIG, on_progress=lambda done, total: progress.append(done)
    )

    assert (pages_done, page_count, completed) == (PAGES, PAGES, True)
    # Первый вызов — уже готовые страницы, дальше по одной на каждую отрендеренную
    assert progress == [3, 4, 5, 6]
    assert {n: _png(out_dir, n).stat().st_mtime_ns for n in (1, 2, 3)} == mtimes_before
    assert not list(out_dir.glob("*.tmp-*")), "остался tmp-файл атомарной записи"


def _ready_pages(out_dir):
    """Номера (1-based) страниц, PNG которых уже на диске."""
    return {n for n in range(1, PAGES + 1) if _png(out_dir, n).exists()}


def test_wanted_page_is_rendered_first(tmp_path, pdf_path, monkeypatch):
    out_dir = tmp_path / f"{STEM}-png"
    _convert_pdf_to_directory_sync(pdf_path, out_dir, STEM, _CONFIG)
    for n in (4, 5, 6):
        _png(out_dir, n).unlink()

    # Конвертер читает heartbeat перед каждой страницей — там же снимаем готовые PNG.
    # Порядок рендеринга — разности соседних снимков: без таймингов и mtime, которые
    # у файлов, записанных за миллисекунды, могут совпасть.
    snapshots = []

    def watching_last_page(_dir):
        snapshots.append(_ready_pages(out_dir))
        return time.time(), 6  # вьюер смотрит последнюю страницу, heartbeat свежий

    monkeypatch.setattr(pdf_service, "read_watch", watching_last_page)

    _convert_pdf_to_directory_sync(pdf_path, out_dir, STEM, _CONFIG)

    snapshots.append(_ready_pages(out_dir))
    rendered = [sorted(after - before) for before, after in zip(snapshots, snapshots[1:])]
    assert rendered == [[6], [4], [5]]


# ---------------------------------------------------------------------------
# Сквозные сценарии: partial в _meta.json и resume_pdf_conversion
# ---------------------------------------------------------------------------


@pytest.fixture()
def cache_root(tmp_path, monkeypatch):
    root = tmp_path / "data.cache"
    root.mkdir()
    monkeypatch.setattr(cache_service_module, "CACHE_DIRECTORY", str(root))
    return root


def _meta(cache_root, archive_name):
    return json.loads((cache_root / archive_name / "_meta.json").read_text(encoding="utf-8"))


def _assert_lock_released(cache_root, archive_name):
    archive_dir = cache_root / archive_name
    assert not (archive_dir / "_prepare.lockdir").exists()
    assert not (archive_dir / "_prepare.json").exists()
    assert not (archive_dir / "_work").exists()


def test_standalone_pdf_partial_then_resume(tmp_path, cache_root, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    source = data_dir / "00002-TST.pdf"
    _make_pdf(source)

    monkeypatch.setattr(pdf_service, "PDF_CONVERT_IDLE_TIMEOUT_SECONDS", -1)
    _run(convert_standalone_pdf(source, "00002-TST"))

    entry = _meta(cache_root, "00002-TST")["files"][0]
    assert entry["status"] == "partial"
    assert (entry["pages"], entry["pages_done"]) == (PAGES, 1)
    png_dir = cache_root / "00002-TST" / "00002-TST-png"
    first_page_mtime = (png_dir / "00002-TST_0001.png").stat().st_mtime_ns

    # Без зрителя запуск рендерит окно тишины от своего старта — маленький PDF успевает целиком
    monkeypatch.setattr(pdf_service, "PDF_CONVERT_IDLE_TIMEOUT_SECONDS", 20)
    _run(resume_pdf_conversion("00002-TST", "00002-TST-png"))

    entry = _meta(cache_root, "00002-TST")["files"][0]
    assert "status" not in entry and "pages_done" not in entry
    assert len(list(png_dir.glob("*.png"))) == PAGES
    assert (png_dir / "00002-TST_0001.png").stat().st_mtime_ns == first_page_mtime
    _assert_lock_released(cache_root, "00002-TST")


def test_zip_pdf_partial_then_resume(tmp_path, cache_root, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pdf = tmp_path / "report.pdf"
    _make_pdf(pdf)
    source = data_dir / "00001-TST.zip"
    with zipfile.ZipFile(source, "w") as zf:
        zf.write(pdf, "dir1/report.pdf")

    monkeypatch.setattr(pdf_service, "PDF_CONVERT_IDLE_TIMEOUT_SECONDS", -1)
    _run(prepare_archive_cache("00001-TST", source))

    entry = _meta(cache_root, "00001-TST")["files"][0]
    assert entry["status"] == "partial"
    assert entry["pages_done"] == 1
    assert entry["png_dir"].replace("\\", "/") == "dir1/report-png"

    # Повторная подготовка валидного кеша не трогает partial (без purge)
    _run(prepare_archive_cache("00001-TST", source))
    assert _meta(cache_root, "00001-TST")["files"][0]["status"] == "partial"

    # Пауза снова: докрутка добавляет страницу и обновляет pages_done
    _run(resume_pdf_conversion("00001-TST", "dir1/report-png"))
    assert _meta(cache_root, "00001-TST")["files"][0]["pages_done"] == 2
    _assert_lock_released(cache_root, "00001-TST")

    monkeypatch.setattr(pdf_service, "PDF_CONVERT_IDLE_TIMEOUT_SECONDS", 20)
    _run(resume_pdf_conversion("00001-TST", "dir1/report-png"))

    meta = _meta(cache_root, "00001-TST")
    entry = meta["files"][0]
    assert "status" not in entry and "pages_done" not in entry
    png_dir = cache_root / "00001-TST" / "dir1" / "report-png"
    assert len(list(png_dir.glob("*.png"))) == PAGES
    assert meta["cache_size_bytes"] >= sum(p.stat().st_size for p in png_dir.glob("*.png"))
    _assert_lock_released(cache_root, "00001-TST")


def test_resume_failure_closes_dir_on_ready_pages(tmp_path, cache_root, monkeypatch):
    """Сбой докрутки закрывает директорию на готовых страницах: иначе /pages считал бы её
    частичной и на каждый опрос вьюера ставил холостую докрутку, а вьюер ждал бы
    недостающих страниц бесконечно."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    pdf = tmp_path / "report.pdf"
    _make_pdf(pdf)
    source = data_dir / "00004-TST.zip"
    with zipfile.ZipFile(source, "w") as zf:
        zf.write(pdf, "report.pdf")
    monkeypatch.setattr(pdf_service, "PDF_CONVERT_IDLE_TIMEOUT_SECONDS", -1)
    _run(prepare_archive_cache("00004-TST", source))
    assert _meta(cache_root, "00004-TST")["files"][0]["status"] == "partial"

    async def broken_extract(*_args):
        raise FileNotFoundError("ZIP member not found: report.pdf")

    monkeypatch.setattr(prepare_service, "extract_single_member", broken_extract)
    _run(resume_pdf_conversion("00004-TST", "report-png"))

    entry = _meta(cache_root, "00004-TST")["files"][0]
    assert entry["status"] == "error"
    assert entry["pages"] == 1
    assert "pages_done" not in entry
    png_dir = cache_root / "00004-TST" / "report-png"
    assert (png_dir / "_pages_total.txt").read_text() == "1"
    assert not is_partial(png_dir)
    _assert_lock_released(cache_root, "00004-TST")

    # Повторная докрутка ничего не делает: до распаковки не доходит, meta не меняется
    extract_calls = []

    async def spy_extract(*args):
        extract_calls.append(args)

    monkeypatch.setattr(prepare_service, "extract_single_member", spy_extract)
    _run(resume_pdf_conversion("00004-TST", "report-png"))

    assert extract_calls == []
    assert _meta(cache_root, "00004-TST")["files"][0] == entry
    _assert_lock_released(cache_root, "00004-TST")


def test_resume_ignores_complete_entry(tmp_path, cache_root):
    """Завершённая запись не докручивается и lock не создаётся."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    source = data_dir / "00003-TST.pdf"
    _make_pdf(source)
    _run(convert_standalone_pdf(source, "00003-TST"))
    assert "status" not in _meta(cache_root, "00003-TST")["files"][0]

    _run(resume_pdf_conversion("00003-TST", "00003-TST-png"))

    _assert_lock_released(cache_root, "00003-TST")
