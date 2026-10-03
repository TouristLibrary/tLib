# Version 6.1 - 03.10.2026 10:17:22 GMT
# PDF to PNG Conversion Service для TlibWebApp
# Описание: Конвертация PDF файлов в PNG страницы.
#           Использует PyMuPDF (fitz) для рендеринга PDF страниц.
#           Рендер и запись PNG — в процессе-воркере (pdf_render_worker), по одной странице:
#           PyMuPDF держит GIL весь рендер, в потоке главного процесса он морил бы цикл событий.
#           Пиковое потребление RAM — одна страница, а не весь документ.
#           Lock на уровне архива обеспечивается cache_prepare_service.
#           Конфигурация через PDF_TO_PNG_* параметры в config.py.
# 5.0: конвертация, пока смотрят. Готовые PNG пропускаются (докрутка частичной директории),
#      первой рендерится страница, которую показывает вьюер (want из _watch.json), запуск
#      встаёт на паузу через PDF_CONVERT_IDLE_TIMEOUT_SECONDS тишины heartbeat.
#      PNG пишется атомарно (tmp + os.replace): готовая страница больше не перерисовывается,
#      поэтому обрыв записи при рестарте не должен оставлять битый файл.
#      Результат — (pages_done, page_count, total_size, completed).
# 5.1: рендер по окну просмотра (cache_watch.first_missing_in_window): без свежего heartbeat —
#      первые PDF_CONVERT_PREWARM_PAGES страниц, со свежим — не дальше PDF_CONVERT_LOOKAHEAD_PAGES
#      от want; окно готово — пауза. Правило «первая страница запуска рендерится всегда» убрано:
#      опрос вьюера раз в 2 с докручивал бы по нему весь PDF. Результат —
#      (pages_done, page_count, rendered, completed); одна INFO-строка «PDF conversion run» на запуск.
# 5.2: generate_png_filename переехал в services/cache/cache_service.py — имена PNG нужны и окну
#      рендера (cache_watch) без циклического импорта; импорт из этого модуля продолжает работать.
# 5.3: render_ms (длительность запуска в thread pool) в «PDF conversion run».
# 6.0: рендер страницы и page_count — в spawn-процессе (ProcessPoolExecutor, PDF_RENDER_WORKERS):
#      цикл окна, on_progress и логи остаются в потоке. Воркер умер (BrokenProcessPool) — пул
#      пересоздаётся следующим рендером, запуск завершается ошибкой, сервер жив.
#      shutdown_render_pool() — для lifespan shutdown.
# 6.1: shutdown_render_pool ждёт выхода воркера (wait=True) — uvicorn после lifespan переподнимает
#      SIGTERM и умирает без atexit, и не дошедший sentinel оставил бы воркер жить до SIGKILL.

import time
import asyncio
import logging
import threading
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Tuple
from dataclasses import dataclass

# Импорт конфигурации
from config import (
    PDF_TO_PNG_ENABLED,
    PDF_TO_PNG_DPI,
    PDF_TO_PNG_COLORSPACE,
    PDF_TO_PNG_ALPHA,
    PDF_CONVERT_LOOKAHEAD_PAGES,
    PDF_RENDER_WORKERS,
)

# Импорт логгеров
from logging_config import app_logger, log_with_data

# Окно рендера по heartbeat просмотра PNG-директории; имена PNG страниц
from services.cache.cache_watch import first_missing_in_window
from services.cache.cache_service import generate_png_filename

# Проверка наличия PyMuPDF
try:
    import fitz  # PyMuPDF
    # Воркер импортирует fitz на верхнем уровне — без PyMuPDF модуль не грузится
    from services.conversion import pdf_render_worker
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False
    if PDF_TO_PNG_ENABLED:
        app_logger.warning(
            "PDF_TO_PNG_ENABLED=True but PyMuPDF is not installed. "
            "Install with: pip install pymupdf"
        )


# ============================================================================
# ТИПЫ ДАННЫХ
# ============================================================================

@dataclass(frozen=True)
class ConversionConfig:
    """Конфигурация конвертации (неизменяемая)."""
    dpi: int
    zoom: float
    colorspace: str
    alpha: bool


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

def get_default_config() -> ConversionConfig:
    """Возвращает конфигурацию из config.py."""
    zoom = PDF_TO_PNG_DPI / 72.0  # Масштабный коэффициент
    return ConversionConfig(
        dpi=PDF_TO_PNG_DPI,
        zoom=zoom,
        colorspace=PDF_TO_PNG_COLORSPACE,
        alpha=PDF_TO_PNG_ALPHA,
    )


def count_pdf_pages(pdf_path: Path) -> int:
    """
    Быстро возвращает число страниц PDF без рендеринга.
    Открывает документ, читает len(doc), закрывает.
    Занимает < 100 мс даже для больших PDF.

    Args:
        pdf_path: путь к PDF файлу

    Returns:
        Число страниц, 0 при ошибке или если PyMuPDF не установлен
    """
    if not HAS_PYMUPDF:
        return 0
    try:
        doc = fitz.open(pdf_path)
        count = len(doc)
        doc.close()
        return count
    except Exception:
        return 0


# ============================================================================
# ПРОЦЕСС-ВОРКЕР РЕНДЕРА
# ============================================================================

# Пул создаётся при первом рендере: прогрев на старте не делаем — ~1 с spawn платит первый
# читатель после рестарта. Lock — два рендер-потока (разные архивы) могут прийти сюда
# одновременно и создать по пулу.
_render_pool = None
_render_pool_lock = threading.Lock()


def _get_pool() -> ProcessPoolExecutor:
    """Пул процессов-воркеров рендера (spawn — см. docstring pdf_render_worker)."""
    global _render_pool
    with _render_pool_lock:
        if _render_pool is None:
            _render_pool = ProcessPoolExecutor(
                max_workers=PDF_RENDER_WORKERS,
                mp_context=multiprocessing.get_context("spawn"),
                initializer=pdf_render_worker.ignore_stop_signals,
            )
        return _render_pool


def _reset_pool(pool: ProcessPoolExecutor) -> None:
    """
    Сломанный пул (воркер умер: segfault MuPDF, OOM killer) задач не принимает —
    следующий рендер создаст новый. Сбрасывается только этот пул: второй рендер-поток мог
    получить тот же BrokenProcessPool позже, чем третий уже создал исправный пул.
    """
    global _render_pool
    with _render_pool_lock:
        if _render_pool is pool:
            _render_pool = None
    pool.shutdown(wait=False)


def shutdown_render_pool() -> None:
    """
    Останавливает воркер на lifespan shutdown. Uvicorn дожидается фоновых задач запросов
    (рендер окна) до lifespan, поэтому пул здесь простаивает и воркер выходит сразу;
    cancel_futures — не дошедшие до воркера страницы не рендерятся: процесс всё равно завершается.

    wait=True: после lifespan uvicorn восстанавливает обработчики и переподнимает SIGTERM —
    процесс умирает мгновенно, без atexit и _python_exit. При wait=False sentinel идёт к воркеру
    через feeder-поток и pipe наперегонки с этим сигналом; проиграл — воркер, игнорирующий
    SIGTERM, висит на очереди задач до SIGKILL по TimeoutStopSec. Ожидание — миллисекунды.
    """
    global _render_pool
    with _render_pool_lock:
        pool, _render_pool = _render_pool, None
    if pool is not None:
        pool.shutdown(wait=True, cancel_futures=True)


# ============================================================================
# STREAMING: РЕНДЕРИНГ СТРАНИЦЫ ЗА СТРАНИЦЕЙ С НЕМЕДЛЕННОЙ ЗАПИСЬЮ НА ДИСК
# ============================================================================

def _convert_pdf_to_directory_sync(
    pdf_path: Path,
    output_dir: Path,
    pdf_stem: str,
    config: ConversionConfig,
    on_progress=None
) -> Tuple[int, int, int, bool]:
    """
    Рендерит недостающие страницы окна просмотра PDF в PNG.
    Страницы рендерятся и сохраняются по одной в процессе-воркере (render_page),
    этот поток только выбирает следующую и ждёт её: в нём GIL рендером не занят.
    Пиковое потребление RAM — одна страница, а не весь документ.
    Синхронная функция для thread pool.

    Каждая страница рендерится один раз: готовые PNG (докрутка после паузы) пропускаются.
    Перед каждой страницей окно пересчитывается по heartbeat просмотра (_watch.json,
    first_missing_in_window): вьюер смотрит страницу want — рендерится первая недостающая
    из PDF_CONVERT_LOOKAHEAD_PAGES страниц от неё (прыжок на дальнюю страницу рендерит её
    первой); зрителя нет — первые PDF_CONVERT_PREWARM_PAGES. Окно готово — пауза.

    Args:
        pdf_path: Путь к PDF файлу
        output_dir: Директория для сохранения PNG
        pdf_stem: Имя PDF без расширения (для имён файлов)
        config: Конфигурация рендеринга
        on_progress: опциональный callback(done, total) после каждой страницы

    Returns:
        (pages_done, page_count, rendered, completed) — rendered: страниц за этот запуск

    Raises:
        BrokenProcessPool: воркер умер — пул сброшен, запуск завершается ошибкой
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    pool = _get_pool()
    i = None
    try:
        page_count = pool.submit(pdf_render_worker.page_count, str(pdf_path)).result()

        existing = {p.name for p in output_dir.glob("*.png")}
        done = {i for i in range(page_count) if generate_png_filename(pdf_stem, i) in existing}

        if on_progress:
            on_progress(len(done), page_count)

        rendered = 0

        while True:
            i = first_missing_in_window(output_dir, pdf_stem, page_count, PDF_CONVERT_LOOKAHEAD_PAGES)
            if i is None:
                break

            start_time = time.perf_counter()

            file_path = output_dir / generate_png_filename(pdf_stem, i)
            pool.submit(
                pdf_render_worker.render_page, str(pdf_path), i, str(file_path),
                config.zoom, config.colorspace, config.alpha
            ).result()

            done.add(i)
            rendered += 1

            render_time = time.perf_counter() - start_time
            app_logger.debug(f"Page {i + 1}/{page_count} rendered in {render_time:.2f}s")

            if on_progress:
                on_progress(len(done), page_count)

    except BrokenProcessPool:
        # Без retry: если страница роняет MuPDF, повтор уронил бы и новый воркер
        _reset_pool(pool)
        log_with_data(logging.WARNING, "PDF render worker died (BrokenProcessPool), pool reset",
                      pdf=pdf_path.as_posix(), page=None if i is None else i + 1)
        raise

    return len(done), page_count, rendered, len(done) == page_count


async def convert_pdf_to_directory(
    pdf_path: Path,
    output_dir: Path,
    pdf_stem: str,
    on_progress=None
) -> Tuple[bool, object]:
    """
    Конвертирует окно просмотра PDF в PNG директорию (streaming: одна страница за раз).

    Рендерит и сохраняет страницы по одной в процессе-воркере.
    Не держит все PNG в памяти одновременно. Готовые PNG пропускаются,
    готовое окно просмотра — пауза (см. _convert_pdf_to_directory_sync).

    Args:
        pdf_path: Путь к PDF файлу
        output_dir: Директория для сохранения PNG
        pdf_stem: Имя PDF без расширения (для имён файлов)
        on_progress: опциональный callback(done, total) после каждой страницы

    Returns:
        (True, (pages_done, page_count, rendered, completed)) - при успехе
            (пауза — тоже успех, completed=False)
        (False, error_message: str) - при ошибке
    """
    if not HAS_PYMUPDF:
        return False, "PyMuPDF not installed"

    if not pdf_path.exists():
        return False, "Source PDF not found"

    try:
        config = get_default_config()

        loop = asyncio.get_event_loop()
        start_time = time.perf_counter()
        pages_done, page_count, rendered, completed = await loop.run_in_executor(
            None,
            _convert_pdf_to_directory_sync,
            pdf_path, output_dir, pdf_stem, config, on_progress
        )
        render_ms = round((time.perf_counter() - start_time) * 1000)

        if page_count == 0:
            return False, "No pages in PDF"

        # Наблюдаемость: сумма rendered по этим строкам — «страниц в час»; полных конвертаций
        # при рендере по окну почти нет, их счётчик нагрузку больше не показывает.
        # render_ms рядом с extract_ms докрутки — цена перераспаковки против цены рендера
        log_with_data(logging.INFO, "PDF conversion run", png_dir=output_dir.as_posix(),
                      rendered=rendered, done=pages_done, total=page_count, completed=completed,
                      render_ms=render_ms)

        return True, (pages_done, page_count, rendered, completed)

    except Exception as e:
        app_logger.error(f"Error converting PDF to directory: {e}", exc_info=True)
        return False, f"Conversion error: {str(e)}"
