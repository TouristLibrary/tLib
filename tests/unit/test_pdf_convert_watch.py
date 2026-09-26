# Version 2.1 - 26.09.2026 11:49:27 GMT
# Тесты рендера PDF по окну просмотра (services/conversion/pdf_to_png_service.py,
# services/cache/cache_watch.first_missing_in_window, cache_prepare_service.resume_pdf_conversion)
# Описание: Без свежего heartbeat конвертер рендерит только первые K страниц (прогрев), со свежим —
#           окно из N страниц от want; страницы позади want ждут, пока want туда вернётся.
#           Готовые PNG не перерисовываются. Докрутка — no-op без lock и распаковки, пока впереди
#           от want готова половина окна (гистерезис). Сквозные сценарии: standalone PDF и PDF
#           из ZIP уходят в partial и докручиваются через want; «В кэш» учитывает подготовку;
#           сбой докрутки переводит запись в error и освобождает lock.
#           PDF генерируется PyMuPDF; без fitz тесты пропускаются.
# 1.1: порядок рендеринга — по снимкам готовых PNG перед каждой страницей (без mtime);
#      сбой докрутки закрывает директорию на готовых страницах, повторная докрутка — no-op.
# 2.0: переписаны под окно просмотра (K/N подменяются малыми) вместо окна тишины IDLE_TIMEOUT.
# 2.1: ensure_cache_space вызывается в конце запуска с cache_size_bytes из записанной meta;
#      повторная подготовка валидного кеша и холостая докрутка место не освобождают.

import asyncio
import json
import time
import zipfile

import pytest

fitz = pytest.importorskip("fitz")

import services.cache.cache_prepare_service as prepare_service
import services.cache.cache_service as cache_service_module
import services.cache.cache_watch as cache_watch_module
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


def _png(out_dir, page_num, stem=STEM):
    """Путь к PNG страницы (1-based)."""
    return out_dir / generate_png_filename(stem, page_num - 1)


def _ready_pages(out_dir, stem=STEM):
    """Номера (1-based) страниц, PNG которых уже на диске."""
    return {n for n in range(1, PAGES + 1) if _png(out_dir, n, stem).exists()}


def _watch(png_dir, want, age=0.0):
    """Heartbeat вьюера, как его пишет /pages; age — давность в секундах."""
    png_dir.mkdir(parents=True, exist_ok=True)
    (png_dir / "_watch.json").write_text(
        json.dumps({"ts": time.time() - age, "want": want}), encoding="utf-8"
    )


@pytest.fixture()
def pdf_path(tmp_path):
    path = tmp_path / f"{STEM}.pdf"
    _make_pdf(path)
    return path


@pytest.fixture()
def window(monkeypatch):
    """Задаёт окно рендера малым для PDF из PAGES страниц: window(K прогрева, N вперёд от want)."""
    def set_window(prewarm, lookahead):
        monkeypatch.setattr(cache_watch_module, "PDF_CONVERT_PREWARM_PAGES", prewarm)
        monkeypatch.setattr(pdf_service, "PDF_CONVERT_LOOKAHEAD_PAGES", lookahead)
        monkeypatch.setattr(prepare_service, "PDF_CONVERT_LOOKAHEAD_PAGES", lookahead)
    return set_window


# ---------------------------------------------------------------------------
# Конвертер: окно прогрева, окно от want, пропуск готовых
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("heartbeat_age", [None, cache_watch_module.PDF_CONVERT_IDLE_TIMEOUT_SECONDS + 5],
                         ids=["no_heartbeat", "stale_heartbeat"])
def test_without_viewer_renders_prewarm_pages(tmp_path, pdf_path, window, heartbeat_age):
    """Зрителя нет (heartbeat нет или он старше IDLE_TIMEOUT) — только первые K страниц."""
    window(2, 4)
    out_dir = tmp_path / f"{STEM}-png"
    if heartbeat_age is not None:
        _watch(out_dir, want=4, age=heartbeat_age)

    result = _convert_pdf_to_directory_sync(pdf_path, out_dir, STEM, _CONFIG)

    assert result == (2, PAGES, 2, False)
    assert _ready_pages(out_dir) == {1, 2}


def test_fresh_heartbeat_renders_window_from_want(tmp_path, pdf_path, window):
    """Смотрят 4-ю страницу, N=2 — рендерятся 4 и 5; страницы до want не трогаются."""
    window(2, 2)
    out_dir = tmp_path / f"{STEM}-png"
    _watch(out_dir, want=4)

    result = _convert_pdf_to_directory_sync(pdf_path, out_dir, STEM, _CONFIG)

    assert result == (2, PAGES, 2, False)
    assert _ready_pages(out_dir) == {4, 5}


def test_window_follows_want(tmp_path, pdf_path, window, monkeypatch):
    """Вьюер листает во время запуска — окно сдвигается за want и дорендеривает."""
    window(2, 2)
    out_dir = tmp_path / f"{STEM}-png"

    # Окно пересчитывается перед каждой страницей — там же снимаем готовые PNG.
    # Порядок рендеринга — разности соседних снимков: без таймингов и mtime, которые
    # у файлов, записанных за миллисекунды, могут совпасть.
    snapshots = []

    def viewer(_dir):
        snapshots.append(_ready_pages(out_dir))
        # смотрят 4-ю страницу, пока её окно не готово, потом возвращаются к началу
        return time.time(), (4 if not _png(out_dir, 5).exists() else 1)

    monkeypatch.setattr(cache_watch_module, "read_watch", viewer)

    _convert_pdf_to_directory_sync(pdf_path, out_dir, STEM, _CONFIG)

    rendered = [sorted(after - before) for before, after in zip(snapshots, snapshots[1:])]
    assert rendered == [[4], [5], [1], [2]]
    assert _ready_pages(out_dir) == {1, 2, 4, 5}


def test_resume_renders_only_missing_pages(tmp_path, pdf_path, window):
    window(3, 3)
    out_dir = tmp_path / f"{STEM}-png"
    _convert_pdf_to_directory_sync(pdf_path, out_dir, STEM, _CONFIG)
    assert _ready_pages(out_dir) == {1, 2, 3}
    mtimes_before = {n: _png(out_dir, n).stat().st_mtime_ns for n in (1, 2, 3)}

    _watch(out_dir, want=4)
    progress = []
    result = _convert_pdf_to_directory_sync(
        pdf_path, out_dir, STEM, _CONFIG, on_progress=lambda done, total: progress.append(done)
    )

    assert result == (PAGES, PAGES, 3, True)
    # Первый вызов — уже готовые страницы, дальше по одной на каждую отрендеренную
    assert progress == [3, 4, 5, 6]
    assert {n: _png(out_dir, n).stat().st_mtime_ns for n in (1, 2, 3)} == mtimes_before
    assert not list(out_dir.glob("*.tmp-*")), "остался tmp-файл атомарной записи"


# ---------------------------------------------------------------------------
# Сквозные сценарии: partial в _meta.json и resume_pdf_conversion
# ---------------------------------------------------------------------------


class _Collector:
    """Stub StatsCollector: считает инкременты «В кэш»."""

    def __init__(self):
        self.cached = 0

    def record_cache_prepared(self):
        self.cached += 1


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


def _make_zip(tmp_path, archive_name, member="dir1/report.pdf"):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    pdf = tmp_path / "report.pdf"
    _make_pdf(pdf)
    source = data_dir / f"{archive_name}.zip"
    with zipfile.ZipFile(source, "w") as zf:
        zf.write(pdf, member)
    return source


def test_standalone_pdf_partial_then_resume(tmp_path, cache_root, window, monkeypatch):
    window(2, 4)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    source = data_dir / "00002-TST.pdf"
    _make_pdf(source)
    collector = _Collector()
    space = []
    monkeypatch.setattr(prepare_service, "ensure_cache_space", space.append)

    _run(convert_standalone_pdf(source, "00002-TST", collector))

    meta = _meta(cache_root, "00002-TST")
    entry = meta["files"][0]
    assert entry["status"] == "partial"
    assert (entry["pages"], entry["pages_done"]) == (PAGES, 2)
    assert collector.cached == 1, "подготовка с PDF на паузе учитывается в «В кэш»"
    # Место освобождается после записи meta, по фактическому размеру папки
    assert space == [meta["cache_size_bytes"]]
    png_dir = cache_root / "00002-TST" / "00002-TST-png"
    first_page_mtime = (png_dir / "00002-TST_0001.png").stat().st_mtime_ns

    # Смотрят 3-ю страницу: окно [3, 6] докручивает документ до конца
    _watch(png_dir, want=3)
    _run(resume_pdf_conversion("00002-TST", "00002-TST-png"))

    meta = _meta(cache_root, "00002-TST")
    entry = meta["files"][0]
    assert "status" not in entry and "pages_done" not in entry
    assert space[1:] == [meta["cache_size_bytes"]]
    assert len(list(png_dir.glob("*.png"))) == PAGES
    assert (png_dir / "00002-TST_0001.png").stat().st_mtime_ns == first_page_mtime
    _assert_lock_released(cache_root, "00002-TST")


def test_zip_pdf_partial_then_resume(tmp_path, cache_root, window, monkeypatch):
    window(2, 4)
    source = _make_zip(tmp_path, "00001-TST")
    collector = _Collector()
    space = []
    monkeypatch.setattr(prepare_service, "ensure_cache_space", space.append)

    _run(prepare_archive_cache("00001-TST", source, collector))

    prepared_size = _meta(cache_root, "00001-TST")["cache_size_bytes"]
    entry = _meta(cache_root, "00001-TST")["files"][0]
    assert entry["status"] == "partial"
    assert entry["pages_done"] == 2
    assert entry["png_dir"].replace("\\", "/") == "dir1/report-png"
    assert collector.cached == 1
    # Место освобождается после записи meta, по фактическому размеру папки
    assert space == [prepared_size]
    png_dir = cache_root / "00001-TST" / "dir1" / "report-png"

    # Повторная подготовка валидного кеша не трогает partial (без purge) и не считается
    _run(prepare_archive_cache("00001-TST", source, collector))
    assert _meta(cache_root, "00001-TST")["files"][0]["status"] == "partial"
    assert collector.cached == 1

    # Без зрителя окно — первые K страниц, они готовы: докрутка ничего не делает
    _run(resume_pdf_conversion("00001-TST", "dir1/report-png"))
    assert _meta(cache_root, "00001-TST")["files"][0]["pages_done"] == 2
    assert space == [prepared_size], "валидный кеш и холостая докрутка место не освобождают"

    # Смотрят 2-ю: впереди готова одна страница из N/2=2 — докрутка рендерит окно [2, 5]
    _watch(png_dir, want=2)
    _run(resume_pdf_conversion("00001-TST", "dir1/report-png"))
    meta = _meta(cache_root, "00001-TST")
    assert meta["files"][0]["pages_done"] == 5
    assert _ready_pages(png_dir, "report") == {1, 2, 3, 4, 5}
    # Место освобождается по размеру, записанному этой докруткой
    assert space == [prepared_size, meta["cache_size_bytes"]]
    _assert_lock_released(cache_root, "00001-TST")

    # Смотрят 5-ю: 6-й нет — докрутка дорендеривает её, запись завершена
    _watch(png_dir, want=5)
    _run(resume_pdf_conversion("00001-TST", "dir1/report-png"))

    meta = _meta(cache_root, "00001-TST")
    entry = meta["files"][0]
    assert "status" not in entry and "pages_done" not in entry
    assert len(list(png_dir.glob("*.png"))) == PAGES
    assert meta["cache_size_bytes"] >= sum(p.stat().st_size for p in png_dir.glob("*.png"))
    _assert_lock_released(cache_root, "00001-TST")


def test_resume_skips_when_half_window_ready(tmp_path, cache_root, window, monkeypatch):
    """Гистерезис: впереди от want готово ≥ N/2 страниц — ни lock, ни распаковки, meta не меняется."""
    window(2, 4)
    source = _make_zip(tmp_path, "00005-TST")
    _run(prepare_archive_cache("00005-TST", source))
    meta_before = _meta(cache_root, "00005-TST")

    calls = []

    def spy_lock(*args):
        calls.append(("lock", args))
        return False

    async def spy_extract(*args):
        calls.append(("extract", args))

    monkeypatch.setattr(prepare_service, "_acquire_lock", spy_lock)
    monkeypatch.setattr(prepare_service, "extract_single_member", spy_extract)

    # Смотрят 1-ю: готовы 1 и 2 — это N/2 страниц впереди
    _watch(cache_root / "00005-TST" / "dir1" / "report-png", want=1)
    _run(resume_pdf_conversion("00005-TST", "dir1/report-png"))

    assert calls == []
    assert _meta(cache_root, "00005-TST") == meta_before
    _assert_lock_released(cache_root, "00005-TST")


def test_resume_failure_closes_dir_on_ready_pages(tmp_path, cache_root, window, monkeypatch):
    """Сбой докрутки закрывает директорию на готовых страницах: иначе /pages считал бы её
    частичной и на каждый опрос вьюера ставил холостую докрутку, а вьюер ждал бы
    недостающих страниц бесконечно."""
    window(1, 4)
    source = _make_zip(tmp_path, "00004-TST", member="report.pdf")
    _run(prepare_archive_cache("00004-TST", source))
    assert _meta(cache_root, "00004-TST")["files"][0]["status"] == "partial"
    png_dir = cache_root / "00004-TST" / "report-png"

    async def broken_extract(*_args):
        raise FileNotFoundError("ZIP member not found: report.pdf")

    monkeypatch.setattr(prepare_service, "extract_single_member", broken_extract)
    _watch(png_dir, want=2)
    _run(resume_pdf_conversion("00004-TST", "report-png"))

    entry = _meta(cache_root, "00004-TST")["files"][0]
    assert entry["status"] == "error"
    assert entry["pages"] == 1
    assert "pages_done" not in entry
    assert (png_dir / "_pages_total.txt").read_text() == "1"
    assert not is_partial(png_dir)
    _assert_lock_released(cache_root, "00004-TST")

    # Повторная докрутка ничего не делает: до распаковки не доходит, meta не меняется
    extract_calls = []

    async def spy_extract(*args):
        extract_calls.append(args)

    monkeypatch.setattr(prepare_service, "extract_single_member", spy_extract)
    _watch(png_dir, want=2)
    _run(resume_pdf_conversion("00004-TST", "report-png"))

    assert extract_calls == []
    assert _meta(cache_root, "00004-TST")["files"][0] == entry
    _assert_lock_released(cache_root, "00004-TST")


def test_resume_ignores_complete_entry(tmp_path, cache_root, window):
    """Завершённая запись не докручивается и lock не создаётся."""
    window(PAGES, 4)  # прогрев покрывает весь документ — конвертация завершается сразу
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    source = data_dir / "00003-TST.pdf"
    _make_pdf(source)
    _run(convert_standalone_pdf(source, "00003-TST"))
    assert "status" not in _meta(cache_root, "00003-TST")["files"][0]

    _run(resume_pdf_conversion("00003-TST", "00003-TST-png"))

    _assert_lock_released(cache_root, "00003-TST")
