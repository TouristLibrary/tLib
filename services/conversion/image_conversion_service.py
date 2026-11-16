# Image Conversion Service для TlibWebApp
# Описание: Оптимизация изображений (ресайз и сжатие) при подготовке кеша архива.
#           PNG остаётся PNG, остальные форматы становятся JPEG.
#           Сам PyMuPDF выполняется в процессе-воркере рендера (pdf_render_worker.optimize_image):
#           в потоке главного процесса он держит GIL, и поиск на стадии картинок растягивается
#           в сотни раз. Здесь — только постановка задачи в пул, ожидание и запись лога.
#           Если размеры уже в лимитах — воркер копирует файл без перекодирования.
#           Lock на уровне архива обеспечивается cache_prepare_service.
#           Конфигурация через IMAGE_TO_JPG_* параметры в config.py.

import logging
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

from config import (
    IMAGE_TO_JPG_ENABLED,
    IMAGE_TO_JPG_MAX_WIDTH,
    IMAGE_TO_JPG_MAX_HEIGHT,
    IMAGE_TO_JPG_QUALITY,
    FITZ_NATIVE_IMAGE_FORMATS,
)
from logging_config import app_logger, log_with_data
from services.conversion.pdf_to_png_service import get_render_pool, reset_render_pool

try:
    # Воркер импортирует fitz на верхнем уровне — без PyMuPDF модуль не грузится
    from services.conversion import pdf_render_worker
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False
    if IMAGE_TO_JPG_ENABLED:
        app_logger.warning(
            "IMAGE_TO_JPG_ENABLED=True but PyMuPDF is not installed. "
            "Install with: pip install pymupdf"
        )


# ============================================================================
# ПУБЛИЧНЫЕ ФУНКЦИИ
# ============================================================================

def optimize_image_sync(
    source_path: Path,
    output_path: Path,
    max_width: int = IMAGE_TO_JPG_MAX_WIDTH,
    max_height: int = IMAGE_TO_JPG_MAX_HEIGHT,
) -> bool:
    """
    Оптимизирует растровое изображение с сохранением пропорций.

    Логика (в процессе-воркере, см. pdf_render_worker.optimize_image):
    - Читает реальные пиксельные размеры через fitz.Pixmap (не PDF-points).
    - Если оба измерения в пределах лимитов — копирует файл без перекодирования.
    - Если нужен ресайз — рендерит через fitz.open + Matrix в целевые пиксели:
        PNG-источник → PNG, все остальные → JPEG.

    Вызывается из cache_pipeline через run_in_executor: поток только ждёт воркер,
    GIL на время PyMuPDF свободен.

    Args:
        source_path: путь к исходному изображению
        output_path: путь для сохранения результата
        max_width: максимальная ширина в пикселях
        max_height: максимальная высота в пикселях

    Returns:
        True при успехе, False при ошибке
    """
    if not HAS_PYMUPDF:
        app_logger.warning("Cannot optimize image: PyMuPDF is not installed")
        return False

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        as_png = source_path.suffix.lower() in FITZ_NATIVE_IMAGE_FORMATS
        pool = get_render_pool()
        src_w, src_h, dst_w, dst_h, copied = pool.submit(
            pdf_render_worker.optimize_image,
            str(source_path), str(output_path),
            max_width, max_height, IMAGE_TO_JPG_QUALITY, as_png,
        ).result()

        if copied:
            log_with_data(
                logging.DEBUG,
                "Image copied (no resize needed)",
                source=source_path.name,
                output=output_path.name,
                size_px=f"{src_w}x{src_h}",
            )
        else:
            log_with_data(
                logging.DEBUG,
                "Image resized",
                source=source_path.name,
                output=output_path.name,
                src_px=f"{src_w}x{src_h}",
                dst_px=f"{dst_w}x{dst_h}",
                output_size=output_path.stat().st_size,
            )
        return True

    except BrokenProcessPool:
        # Без retry: картинка, роняющая MuPDF, уронила бы и новый воркер.
        # Оригинал в кеш положит вызывающий — как при любой другой ошибке оптимизации.
        reset_render_pool(pool)
        log_with_data(logging.WARNING, "Image optimize worker died (BrokenProcessPool), pool reset",
                      source=source_path.name)
        return False
    except Exception as e:
        app_logger.warning(f"Error optimizing image {source_path.name}: {e}")
        return False
