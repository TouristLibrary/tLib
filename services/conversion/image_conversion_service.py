# Version 3.0 - 17.04.2026 00:00:00 GMT
# Image Conversion Service для TlibWebApp
# Описание: Оптимизация изображений (ресайз и сжатие).
#           PNG оптимизируется в своём формате, остальные конвертируются в JPG.
#           Использует fitz.Pixmap для получения реальных пиксельных размеров,
#           fitz.open + Matrix для ресайза с сохранением пропорций.
#           Если размеры уже в лимитах — файл копируется без перекодирования.
#           Lock на уровне архива обеспечивается cache_prepare_service.
#           Конфигурация через IMAGE_TO_JPG_* параметры в config.py.

import shutil
import logging
from pathlib import Path

from config import (
    IMAGE_TO_JPG_ENABLED,
    IMAGE_TO_JPG_MAX_WIDTH,
    IMAGE_TO_JPG_MAX_HEIGHT,
    IMAGE_TO_JPG_QUALITY,
    FITZ_NATIVE_IMAGE_FORMATS,
)
from logging_config import app_logger, log_with_data

try:
    import fitz  # PyMuPDF
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

    Логика:
    - Читает реальные пиксельные размеры через fitz.Pixmap (не PDF-points).
    - Если оба измерения в пределах лимитов — копирует файл без перекодирования.
    - Если нужен ресайз — рендерит через fitz.open + Matrix в целевые пиксели:
        PNG-источник → PNG, все остальные → JPEG.

    Вызывается из cache_pipeline через run_in_executor.

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
        # Читаем реальные пиксельные размеры (fitz.Pixmap даёт пиксели, не points)
        pix = fitz.Pixmap(str(source_path))
        w, h = pix.width, pix.height
        pix = None  # освобождаем память немедленно

        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Ресайз не нужен — копируем байт-в-байт без потери качества
        if w <= max_width and h <= max_height:
            shutil.copyfile(source_path, output_path)
            log_with_data(
                logging.DEBUG,
                "Image copied (no resize needed)",
                source=source_path.name,
                output=output_path.name,
                size_px=f"{w}x{h}",
            )
            return True

        # Нужен ресайз: вычисляем масштаб по пикселям
        scale = min(max_width / w, max_height / h)
        target_w = max(1, round(w * scale))

        with fitz.open(str(source_path)) as doc:
            page = doc[0]
            # Переводим целевые пиксели в matrix-коэффициент через rect (в points)
            m = target_w / page.rect.width
            matrix = fitz.Matrix(m, m)
            out = page.get_pixmap(matrix=matrix, alpha=False)

            if source_path.suffix.lower() in FITZ_NATIVE_IMAGE_FORMATS:
                out.save(str(output_path), "png")
            else:
                out.save(str(output_path), "jpeg", jpg_quality=IMAGE_TO_JPG_QUALITY)

        output_size = output_path.stat().st_size
        target_h = max(1, round(h * scale))

        log_with_data(
            logging.DEBUG,
            "Image resized",
            source=source_path.name,
            output=output_path.name,
            src_px=f"{w}x{h}",
            dst_px=f"{target_w}x{target_h}",
            output_size=output_size,
        )
        return True

    except Exception as e:
        app_logger.warning(f"Error optimizing image {source_path.name}: {e}")
        return False
