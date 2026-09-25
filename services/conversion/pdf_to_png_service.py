# Version 5.0 - 25.09.2026 12:00:00 GMT
# PDF to PNG Conversion Service для TlibWebApp
# Описание: Конвертация PDF файлов в PNG страницы.
#           Использует PyMuPDF (fitz) для рендеринга PDF страниц.
#           Открывает PDF один раз, рендерит и сохраняет страницы по одной.
#           Пиковое потребление RAM — одна страница, а не весь документ.
#           Lock на уровне архива обеспечивается cache_prepare_service.
#           Конфигурация через PDF_TO_PNG_* параметры в config.py.
# 5.0: конвертация, пока смотрят. Готовые PNG пропускаются (докрутка частичной директории),
#      первой рендерится страница, которую показывает вьюер (want из _watch.json), запуск
#      встаёт на паузу через PDF_CONVERT_IDLE_TIMEOUT_SECONDS тишины heartbeat.
#      PNG пишется атомарно (tmp + os.replace): готовая страница больше не перерисовывается,
#      поэтому обрыв записи при рестарте не должен оставлять битый файл.
#      Результат — (pages_done, page_count, total_size, completed).

import os
import time
import asyncio
import logging
from pathlib import Path
from typing import Tuple
from dataclasses import dataclass

# Импорт конфигурации
from config import (
    PDF_TO_PNG_ENABLED,
    PDF_TO_PNG_DPI,
    PDF_TO_PNG_COLORSPACE,
    PDF_TO_PNG_ALPHA,
    PDF_CONVERT_IDLE_TIMEOUT_SECONDS,
)

# Импорт логгеров
from logging_config import app_logger, log_with_data

# Heartbeat просмотра PNG-директории
from services.cache.cache_watch import read_watch

# Проверка наличия PyMuPDF
try:
    import fitz  # PyMuPDF
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


def generate_png_filename(pdf_stem: str, page_num: int) -> str:
    """
    Генерирует имя PNG файла.

    Args:
        pdf_stem: Имя PDF без расширения
        page_num: Номер страницы (0-индексированный)

    Returns:
        Имя файла вида "имяPDF_0001.png"
    """
    return f"{pdf_stem}_{page_num + 1:04d}.png"


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
# STREAMING: РЕНДЕРИНГ СТРАНИЦЫ ЗА СТРАНИЦЕЙ С НЕМЕДЛЕННОЙ ЗАПИСЬЮ НА ДИСК
# ============================================================================

def _next_page(done: set, cursor: int, page_count: int) -> int:
    """Ближайшая недостающая страница (0-based) не раньше cursor, иначе минимальная недостающая."""
    for i in range(cursor, page_count):
        if i not in done:
            return i
    return next(i for i in range(page_count) if i not in done)


def _convert_pdf_to_directory_sync(
    pdf_path: Path,
    output_dir: Path,
    pdf_stem: str,
    config: ConversionConfig,
    on_progress=None
) -> Tuple[int, int, int, bool]:
    """
    Рендерит недостающие страницы PDF в PNG, пока директорию смотрят.
    Открывает PDF один раз, рендерит и сохраняет страницы по одной.
    Пиковое потребление RAM — одна страница, а не весь документ.
    Синхронная функция для thread pool.

    Каждая страница рендерится один раз: готовые PNG (докрутка после паузы) пропускаются.
    Между страницами читается heartbeat просмотра (_watch.json):
    - запрошенная вьюером страница (want) рендерится первой, дальше — по порядку от неё;
    - если heartbeat молчит дольше PDF_CONVERT_IDLE_TIMEOUT_SECONDS (отсчёт от старта запуска
      или последнего heartbeat) — пауза. Первая страница запуска рендерится всегда.

    Args:
        pdf_path: Путь к PDF файлу
        output_dir: Директория для сохранения PNG
        pdf_stem: Имя PDF без расширения (для имён файлов)
        config: Конфигурация рендеринга
        on_progress: опциональный callback(done, total) после каждой страницы

    Returns:
        (pages_done, page_count, total_size_bytes, completed) — total_size только за этот запуск
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if config.colorspace == "gray":
        cs = fitz.csGRAY
    else:
        cs = fitz.csRGB

    matrix = fitz.Matrix(config.zoom, config.zoom)

    doc = fitz.open(pdf_path)
    try:
        page_count = len(doc)
        total_size = 0

        existing = {p.name for p in output_dir.glob("*.png")}
        done = {i for i in range(page_count) if generate_png_filename(pdf_stem, i) in existing}

        if on_progress:
            on_progress(len(done), page_count)

        last_seen = time.time()  # старт запуска: без зрителя рендерим не дольше окна тишины
        cursor = 0
        rendered = 0

        while len(done) < page_count:
            ts, want = read_watch(output_dir)
            last_seen = max(last_seen, ts)
            if rendered > 0 and time.time() - last_seen > PDF_CONVERT_IDLE_TIMEOUT_SECONDS:
                break
            if want is not None and want <= page_count and (want - 1) not in done:
                cursor = want - 1
            i = _next_page(done, cursor, page_count)

            start_time = time.perf_counter()

            page = doc[i]
            pixmap = page.get_pixmap(matrix=matrix, colorspace=cs, alpha=config.alpha)
            png_data = pixmap.tobytes(output="png")
            pixmap = None  # освобождаем память сразу

            # tmp-имя не матчится glob("*.png") и исключается из cache_size_bytes ('.tmp-')
            file_path = output_dir / generate_png_filename(pdf_stem, i)
            tmp_path = file_path.with_name(f"{file_path.name}.tmp-{os.getpid()}")
            tmp_path.write_bytes(png_data)
            os.replace(tmp_path, file_path)
            total_size += len(png_data)
            png_data = None  # освобождаем память сразу

            done.add(i)
            rendered += 1
            cursor = i + 1

            render_time = time.perf_counter() - start_time
            app_logger.debug(f"Page {i + 1}/{page_count} rendered in {render_time:.2f}s")

            if on_progress:
                on_progress(len(done), page_count)

    finally:
        doc.close()

    return len(done), page_count, total_size, len(done) == page_count


async def convert_pdf_to_directory(
    pdf_path: Path,
    output_dir: Path,
    pdf_stem: str,
    on_progress=None
) -> Tuple[bool, object]:
    """
    Конвертирует PDF в PNG директорию (streaming: одна страница за раз), пока её смотрят.

    Открывает PDF один раз, рендерит и сохраняет страницы по одной.
    Не держит все PNG в памяти одновременно. Готовые PNG пропускаются,
    без heartbeat просмотра запуск встаёт на паузу (см. _convert_pdf_to_directory_sync).

    Args:
        pdf_path: Путь к PDF файлу
        output_dir: Директория для сохранения PNG
        pdf_stem: Имя PDF без расширения (для имён файлов)
        on_progress: опциональный callback(done, total) после каждой страницы

    Returns:
        (True, (pages_done, page_count, total_size_bytes, completed)) - при успехе
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
        pages_done, page_count, total_size, completed = await loop.run_in_executor(
            None,
            _convert_pdf_to_directory_sync,
            pdf_path, output_dir, pdf_stem, config, on_progress
        )

        if page_count == 0:
            return False, "No pages in PDF"

        if completed:
            app_logger.debug(f"PDF rendered: {pdf_path.name}, {page_count} pages, {total_size} bytes")
        else:
            # Наблюдаемость: доля пауз среди конвертаций видна по app.log
            log_with_data(logging.INFO, "PDF conversion paused",
                          png_dir=output_dir.as_posix(), done=pages_done, total=page_count)

        return True, (pages_done, page_count, total_size, completed)

    except Exception as e:
        app_logger.error(f"Error converting PDF to directory: {e}", exc_info=True)
        return False, f"Conversion error: {str(e)}"
