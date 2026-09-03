# Version 2.0 - 06.02.2026 00:00:00 GMT
# Cache Prepare Service для TlibWebApp
# Описание: Централизованный сервис подготовки кеша архивов.
#           Единственный владелец _prepare.json и lock-логики.
#           Поддерживает eager caching с per-file readiness.
#           Работает единообразно для ZIP архивов и standalone PDF.

import os
import json
import shutil
import logging
from pathlib import Path
from datetime import datetime, timezone

# Импорт конфигурации
from config import (
    IMAGE_TO_JPG_ENABLED,
    PDF_TO_PNG_ENABLED,
    CACHE_WORK_DIRNAME,
    CACHE_STALE_LOCK_TIMEOUT_MINUTES,
    CACHE_PREPARE_STATUS_FILENAME,
    CACHE_LOCK_DIRNAME,
    CACHE_ZIP_SIZE_MULTIPLIER,
    CACHE_PDF_SIZE_MULTIPLIER,
    CACHE_STATUS_PREPARING, CACHE_STATUS_NONE,
    CACHE_STAGE_STARTING, CACHE_STAGE_CONVERTING,
)

# Импорт логгеров
from logging_config import app_logger, log_with_data

# Импорт вспомогательных сервисов (относительные импорты внутри пакета)
from .cache_service import (
    get_cache_dir,
    get_png_dir_path,
    ensure_cache_space,
    atomic_write_json,
    is_cache_valid
)
from .cache_pipeline import (
    extract_files,
    convert_gps_tracks,
    convert_pdfs,
    convert_images,
    write_meta,
    write_meta_standalone_pdf,
    write_meta_with_error
)


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

def _acquire_lock(archive_name: str) -> bool:
    """
    Захватывает lock через mkdir (atomic на всех ОС).
    Записывает pid+time в info.txt для отладки.
    
    Args:
        archive_name: имя архива
        
    Returns:
        True если lock успешно захвачен, False если уже занят
    """
    cache_dir = get_cache_dir(archive_name)
    lock_dir = cache_dir / CACHE_LOCK_DIRNAME
    
    try:
        lock_dir.mkdir(parents=True, exist_ok=False)
        # Записываем информацию для отладки
        info_file = lock_dir / "info.txt"
        info_file.write_text(
            f"pid: {os.getpid()}\n"
            f"timestamp: {datetime.now(timezone.utc).isoformat()}\n",
            encoding="utf-8"
        )
        return True
    except FileExistsError:
        return False
    except Exception as e:
        app_logger.warning(f"Failed to acquire lock for {archive_name}: {e}")
        return False


def _release_lock(archive_name: str) -> None:
    """
    Освобождает lock (удаляет lockdir).
    
    Args:
        archive_name: имя архива
    """
    cache_dir = get_cache_dir(archive_name)
    lock_dir = cache_dir / CACHE_LOCK_DIRNAME
    
    try:
        if lock_dir.exists():
            shutil.rmtree(lock_dir)
    except Exception as e:
        app_logger.warning(f"Failed to release lock for {archive_name}: {e}")


def write_prepare_status(archive_name: str, *, stage: str, sub: str = "", detail: str = "", pages_total: int = 0, converting_path: str = "") -> None:
    """
    Пишет heartbeat в _prepare.json.
    
    Args:
        archive_name: имя архива
        stage: стадия подготовки (starting, extracting, converting)
        sub: подстадия (gps, pdf, images)
        detail: детали (например, "PDF 3/10")
        pages_total: общее количество страниц PDF (0 = не указано)
        converting_path: путь к конвертируемому PDF внутри архива (пустой = не указано)
    """
    cache_dir = get_cache_dir(archive_name)
    prepare_path = cache_dir / CACHE_PREPARE_STATUS_FILENAME
    
    data = {
        "status": CACHE_STATUS_PREPARING,
        "stage": stage,
        "sub": sub,
        "detail": detail,
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    if pages_total > 0:
        data["pages_total"] = pages_total
    if converting_path:
        data["converting_path"] = converting_path
    
    try:
        atomic_write_json(prepare_path, data)
    except Exception as e:
        app_logger.warning(f"Failed to write prepare status for {archive_name}: {e}")


def read_prepare_status(archive_name: str) -> dict:
    """
    Читает _prepare.json.
    Возвращает {"status":"none","stage":None} если нет/битый.
    
    Args:
        archive_name: имя архива
        
    Returns:
        dict с полями status, stage, detail, updated_at
    """
    cache_dir = get_cache_dir(archive_name)
    prepare_path = cache_dir / CACHE_PREPARE_STATUS_FILENAME
    
    try:
        if not prepare_path.exists():
            return {"status": CACHE_STATUS_NONE, "stage": None}
        
        data = json.loads(prepare_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"status": CACHE_STATUS_NONE, "stage": None}
        
        return data
    except Exception:
        return {"status": CACHE_STATUS_NONE, "stage": None}


def is_preparing(archive_name: str) -> bool:
    """
    Проверяет, идет ли подготовка кеша.
    True если _prepare.json есть и updated_at < 5 мин.
    
    Args:
        archive_name: имя архива
        
    Returns:
        True если подготовка в процессе
    """
    prepare = read_prepare_status(archive_name)
    
    if prepare["status"] != CACHE_STATUS_PREPARING:
        return False
    
    # Проверяем stale timeout
    try:
        updated_at = datetime.fromisoformat(prepare["updated_at"])
        now = datetime.now(timezone.utc)
        age = (now - updated_at).total_seconds() / 60
        
        if age > CACHE_STALE_LOCK_TIMEOUT_MINUTES:
            # Stale - нужна очистка
            return False
        
        return True
    except Exception:
        return False


def _cleanup_stale(archive_name: str) -> None:
    """
    Удаляет _work/, _prepare.lockdir, _prepare.json.
    Каждое удаление обёрнуто в try/except FileNotFoundError.
    
    Args:
        archive_name: имя архива
    """
    cache_dir = get_cache_dir(archive_name)
    
    # Удаляем _work/
    work_dir = cache_dir / CACHE_WORK_DIRNAME
    try:
        if work_dir.exists():
            shutil.rmtree(work_dir)
            app_logger.debug(f"Cleaned stale _work/ for {archive_name}")
    except FileNotFoundError:
        pass
    except Exception as e:
        app_logger.warning(f"Failed to cleanup stale _work/ for {archive_name}: {e}")
    
    # Удаляем _prepare.lockdir
    lock_dir = cache_dir / CACHE_LOCK_DIRNAME
    try:
        if lock_dir.exists():
            shutil.rmtree(lock_dir)
            app_logger.debug(f"Cleaned stale lock for {archive_name}")
    except FileNotFoundError:
        pass
    except Exception as e:
        app_logger.warning(f"Failed to cleanup stale lock for {archive_name}: {e}")
    
    # Удаляем _prepare.json
    prepare_path = cache_dir / CACHE_PREPARE_STATUS_FILENAME
    try:
        prepare_path.unlink(missing_ok=True)
        app_logger.debug(f"Cleaned stale prepare status for {archive_name}")
    except Exception as e:
        app_logger.warning(f"Failed to cleanup stale prepare status for {archive_name}: {e}")


def _purge_old_content(cache_dir: Path) -> None:
    """
    Удаляет всё содержимое cache_dir кроме _prepare.lockdir и _prepare.json.
    Вызывается после acquire_lock, перед extraction/конвертацией.
    
    Args:
        cache_dir: путь к директории кеша
    """
    if not cache_dir.exists():
        return
    
    try:
        for item in cache_dir.iterdir():
            if item.name in (CACHE_LOCK_DIRNAME, CACHE_PREPARE_STATUS_FILENAME):
                continue
            
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            except Exception as e:
                app_logger.warning(f"Failed to purge {item.name}: {e}")
    except Exception as e:
        app_logger.warning(f"Failed to purge old content in {cache_dir}: {e}")


# ============================================================================
# ГЛАВНЫЕ ФУНКЦИИ ПОДГОТОВКИ КЕША
# ============================================================================

async def prepare_archive_cache(archive_name: str, zip_path: Path, stats_collector=None) -> None:
    """
    Главная функция подготовки кеша архива.
    Выполняет последовательную обработку: extraction -> GPS -> PDF -> Images.
    
    Args:
        archive_name: имя архива без расширения
        zip_path: путь к ZIP архиву
        stats_collector: опциональный StatsCollector для учёта кэшированных отчётов
    """
    # Шаг 1: Проверяем актуальность кеша
    if is_cache_valid(archive_name, zip_path):
        app_logger.debug(f"Cache valid for {archive_name}, skipping prepare")
        return
    
    # Шаг 2: Проверяем stale подготовку
    if is_preparing(archive_name):
        app_logger.debug(f"Cache prepare already in progress for {archive_name}")
        return
    else:
        # Если prepare status есть, но stale - очищаем
        prepare = read_prepare_status(archive_name)
        if prepare["status"] == CACHE_STATUS_PREPARING:
            _cleanup_stale(archive_name)
    
    # Шаг 3: Захватываем lock
    if not _acquire_lock(archive_name):
        app_logger.debug(f"Lock busy for {archive_name}, another worker is preparing")
        return
    
    cache_dir = get_cache_dir(archive_name)
    
    try:
        # Шаг 4: Пишем prepare status ДО purge
        write_prepare_status(archive_name, stage=CACHE_STAGE_STARTING)
        
        # Шаг 5: Удаляем старый контент
        _purge_old_content(cache_dir)
        
        # Шаг 6: Освобождаем место в кеше
        # Эвристика: сжатый размер * множитель
        estimated_size = int(zip_path.stat().st_size * CACHE_ZIP_SIZE_MULTIPLIER)
        ensure_cache_space(estimated_size)
        
        # Шаг 7: Извлекаем файлы из архива и собираем информацию
        files_info = await extract_files(archive_name, zip_path, cache_dir, write_prepare_status)

        # Шаг 8: Конвертируем GPS треки и получаем информацию о geo архиве
        geo_archive_info = await convert_gps_tracks(archive_name, zip_path, cache_dir, write_prepare_status)
        
        # Шаг 9: Конвертируем PDF и обновляем files_info
        if PDF_TO_PNG_ENABLED:
            await convert_pdfs(archive_name, zip_path, cache_dir, files_info, write_prepare_status)
        
        # Шаг 10: Конвертируем изображения и обновляем files_info
        if IMAGE_TO_JPG_ENABLED:
            await convert_images(archive_name, zip_path, cache_dir, files_info, write_prepare_status)
        
        # Шаг 11: Удаляем _work/
        work_dir = cache_dir / CACHE_WORK_DIRNAME
        try:
            if work_dir.exists():
                shutil.rmtree(work_dir)
        except Exception as e:
            app_logger.warning(f"Failed to cleanup _work/ for {archive_name}: {e}")
        
        # Шаг 12: Записываем _meta.json атомарно с полной информацией
        await write_meta(archive_name, zip_path, cache_dir, files_info, geo_archive_info)
        
        log_with_data(logging.INFO, "Cache prepared successfully", archive=archive_name)
        if stats_collector is not None:
            try:
                stats_collector.record_cache_prepared()
            except Exception:
                pass

    except Exception as e:
        app_logger.error(f"Error preparing cache for {archive_name}: {e}", exc_info=True)
    finally:
        # Всегда удаляем _prepare.json и освобождаем lock
        prepare_path = cache_dir / CACHE_PREPARE_STATUS_FILENAME
        try:
            prepare_path.unlink(missing_ok=True)
        except Exception:
            pass
        
        _release_lock(archive_name)


async def convert_standalone_pdf(pdf_path: Path, archive_name: str, stats_collector=None) -> None:
    """
    Конвертирует standalone PDF в PNG директорию.
    Работает единообразно с prepare_archive_cache().
    
    Args:
        pdf_path: путь к PDF файлу
        archive_name: имя архива (stem PDF файла)
        stats_collector: опциональный StatsCollector для учёта кэшированных отчётов
    """
    # Шаг 1: Проверяем актуальность кеша
    if is_cache_valid(archive_name, pdf_path):
        app_logger.debug(f"PDF cache valid for {archive_name}, skipping conversion")
        return
    
    # Шаг 2: Проверяем stale подготовку
    if is_preparing(archive_name):
        app_logger.debug(f"PDF conversion already in progress for {archive_name}")
        return
    else:
        # Если prepare status есть, но stale - очищаем
        prepare = read_prepare_status(archive_name)
        if prepare["status"] == CACHE_STATUS_PREPARING:
            _cleanup_stale(archive_name)
    
    # Шаг 3: Захватываем lock
    if not _acquire_lock(archive_name):
        app_logger.debug(f"Lock busy for {archive_name}, another worker is converting")
        return
    
    cache_dir = get_cache_dir(archive_name)
    
    try:
        # Шаг 4: Пишем prepare status ДО purge
        write_prepare_status(archive_name, stage=CACHE_STAGE_CONVERTING, sub="pdf")
        
        # Шаг 5: Удаляем старые PNG
        _purge_old_content(cache_dir)
        
        # Шаг 6: Освобождаем место
        # Эвристика: PDF размер * множитель
        estimated_size = int(pdf_path.stat().st_size * CACHE_PDF_SIZE_MULTIPLIER)
        ensure_cache_space(estimated_size)
        
        # Шаг 7: Конвертируем PDF -> PNG
        from services.conversion.pdf_to_png_service import convert_pdf_to_directory, count_pdf_pages
        
        png_dir = get_png_dir_path(archive_name, pdf_path.name)
        png_dir.mkdir(parents=True, exist_ok=True)  # создаём саму директорию (не только parent)
        
        # Pre-scan: считаем страницы и записываем pages_total до начала рендеринга
        pre_pages = count_pdf_pages(pdf_path)
        if pre_pages > 0:
            (png_dir / "_pages_total.txt").write_text(str(pre_pages))
            write_prepare_status(archive_name, stage=CACHE_STAGE_CONVERTING, sub="pdf",
                                 pages_total=pre_pages, converting_path=pdf_path.name)
        
        def _pdf_progress(done, total):
            pct = int(done / total * 100) if total > 0 else 0
            write_prepare_status(archive_name, stage=CACHE_STAGE_CONVERTING,
                                 sub="pdf", detail=f"{done}/{total} ({pct}%)",
                                 pages_total=total, converting_path=pdf_path.name)
        
        success, result = await convert_pdf_to_directory(pdf_path, png_dir, pdf_path.stem, on_progress=_pdf_progress)
        
        if not success:
            # Записываем ошибку в _meta.json
            await write_meta_with_error(archive_name, pdf_path, str(result))
            return
        
        page_count, total_size = result
        
        # Шаг 8: Записываем _meta.json
        await write_meta_standalone_pdf(archive_name, pdf_path, png_dir, page_count)
        
        log_with_data(logging.INFO, "Standalone PDF converted", pdf=archive_name, pages=page_count)
        if stats_collector is not None:
            try:
                stats_collector.record_cache_prepared()
            except Exception:
                pass

    except Exception as e:
        app_logger.error(f"Error converting standalone PDF {archive_name}: {e}", exc_info=True)
    finally:
        # Всегда удаляем _prepare.json и освобождаем lock
        prepare_path = cache_dir / CACHE_PREPARE_STATUS_FILENAME
        try:
            prepare_path.unlink(missing_ok=True)
        except Exception:
            pass
        
        _release_lock(archive_name)
