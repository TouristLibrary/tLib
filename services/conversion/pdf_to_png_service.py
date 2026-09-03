# Version 4.0 - 28.03.2026 00:00:00 GMT
# PDF to PNG Conversion Service для TlibWebApp
# Описание: Конвертация PDF файлов в PNG страницы.
#           Использует PyMuPDF (fitz) для рендеринга PDF страниц.
#           Открывает PDF один раз, рендерит и сохраняет страницы последовательно.
#           Пиковое потребление RAM — одна страница, а не весь документ.
#           Lock на уровне архива обеспечивается cache_prepare_service.
#           Конфигурация через PDF_TO_PNG_* параметры в config.py.

import time
import asyncio
from pathlib import Path
from typing import Tuple
from dataclasses import dataclass

# Импорт конфигурации
from config import (
    PDF_TO_PNG_ENABLED,
    PDF_TO_PNG_DPI,
    PDF_TO_PNG_COLORSPACE,
    PDF_TO_PNG_ALPHA,
)

# Импорт логгеров
from logging_config import app_logger

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

def _convert_pdf_to_directory_sync(
    pdf_path: Path,
    output_dir: Path,
    pdf_stem: str,
    config: ConversionConfig,
    on_progress=None
) -> Tuple[int, int]:
    """
    Конвертирует все страницы PDF в PNG и сохраняет в директорию.
    Открывает PDF один раз, рендерит и сохраняет страницы последовательно.
    Пиковое потребление RAM — одна страница, а не весь документ.
    Синхронная функция для thread pool.

    Args:
        pdf_path: Путь к PDF файлу
        output_dir: Директория для сохранения PNG
        pdf_stem: Имя PDF без расширения (для имён файлов)
        config: Конфигурация рендеринга
        on_progress: опциональный callback(done, total) после каждой страницы

    Returns:
        (page_count, total_size_bytes)
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

        if on_progress:
            on_progress(0, page_count)

        for i in range(page_count):
            start_time = time.perf_counter()

            page = doc[i]
            pixmap = page.get_pixmap(matrix=matrix, colorspace=cs, alpha=config.alpha)
            png_data = pixmap.tobytes(output="png")
            pixmap = None  # освобождаем память сразу

            filename = generate_png_filename(pdf_stem, i)
            file_path = output_dir / filename
            file_path.write_bytes(png_data)
            total_size += len(png_data)
            png_data = None  # освобождаем память сразу

            render_time = time.perf_counter() - start_time
            app_logger.debug(f"Page {i + 1}/{page_count} rendered in {render_time:.2f}s")

            if on_progress:
                on_progress(i + 1, page_count)

    finally:
        doc.close()

    return page_count, total_size


async def convert_pdf_to_directory(
    pdf_path: Path,
    output_dir: Path,
    pdf_stem: str,
    on_progress=None
) -> Tuple[bool, object]:
    """
    Конвертирует PDF в PNG директорию (streaming: одна страница за раз).

    Открывает PDF один раз, рендерит и сохраняет страницы последовательно.
    Не держит все PNG в памяти одновременно.

    Args:
        pdf_path: Путь к PDF файлу
        output_dir: Директория для сохранения PNG
        pdf_stem: Имя PDF без расширения (для имён файлов)
        on_progress: опциональный callback(done, total) после каждой страницы

    Returns:
        (True, (page_count, total_size_bytes)) - при успехе
        (False, error_message: str) - при ошибке
    """
    if not HAS_PYMUPDF:
        return False, "PyMuPDF not installed"

    if not pdf_path.exists():
        return False, "Source PDF not found"

    try:
        config = get_default_config()

        loop = asyncio.get_event_loop()
        page_count, total_size = await loop.run_in_executor(
            None,
            _convert_pdf_to_directory_sync,
            pdf_path, output_dir, pdf_stem, config, on_progress
        )

        if page_count == 0:
            return False, "No pages in PDF"

        app_logger.debug(f"PDF rendered: {pdf_path.name}, {page_count} pages, {total_size} bytes")

        return True, (page_count, total_size)

    except Exception as e:
        app_logger.error(f"Error converting PDF to directory: {e}", exc_info=True)
        return False, f"Conversion error: {str(e)}"
