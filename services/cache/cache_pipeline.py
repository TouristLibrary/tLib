# Version 2.0 - 08.02.2026 00:00:00 GMT
# Cache Pipeline для TlibWebApp
# Описание: Конвертационные шаги подготовки кеша.
#           Извлечение, конвертация PDF/изображений/GPS, запись meta.
#           Вызывается из cache_prepare_service.

import shutil
import zipfile
import asyncio
from pathlib import Path
from datetime import datetime, timezone

# Импорт конфигурации
from config import (
    GPS_TRACK_EXTENSIONS,
    IMAGE_EXTENSIONS,
    FITZ_NATIVE_IMAGE_FORMATS,
    IMAGE_TO_JPG_MAX_WIDTH,
    IMAGE_TO_JPG_MAX_HEIGHT,
    MAX_FILE_SIZE,
    FILTER_MACOS_METADATA,
    CACHE_META_FILENAME,
    CACHE_WORK_DIRNAME,
    GEO_ARCHIVE_SUFFIX,
    CACHE_STATUS_ERROR,
    CACHE_STAGE_EXTRACTING, CACHE_STAGE_CONVERTING,
)

# Импорт логгеров
from logging_config import app_logger

# Импорт вспомогательных сервисов (относительные импорты внутри пакета)
from services.file_service import decode_zip_filename, is_macos_metadata_file
from .cache_service import (
    get_cache_dir,
    get_png_dir_path,
    atomic_write_json
)


# ============================================================================
# ФУНКЦИИ ИЗВЛЕЧЕНИЯ И КОНВЕРТАЦИИ
# ============================================================================

def read_zip_toc(zip_path: Path) -> list[dict]:
    """
    Читает центральный каталог ZIP без извлечения файлов (< 1 мс).
    Применяет те же фильтры, что и extract_files.

    Args:
        zip_path: путь к ZIP архиву

    Returns:
        [{"zip_path": str, "original_filename": str, "kind": str,
          "size": int, "file_ext": str}, ...]
    """
    result = []
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if FILTER_MACOS_METADATA and is_macos_metadata_file(info.filename):
                continue
            if info.file_size > MAX_FILE_SIZE:
                continue
            decoded_name = decode_zip_filename(info.filename)
            file_ext = Path(decoded_name).suffix.lower()
            if file_ext == '.pdf':
                kind = 'pdf'
            elif file_ext in IMAGE_EXTENSIONS:
                kind = 'image'
            elif file_ext in GPS_TRACK_EXTENSIONS:
                kind = 'track'
            else:
                kind = 'other'
            result.append({
                "zip_path": decoded_name,
                "original_filename": info.filename,
                "kind": kind,
                "size": info.file_size,
                "file_ext": file_ext,
            })
    return result


async def extract_files(archive_name: str, zip_path: Path, cache_dir: Path,
                        write_status_callback) -> list[dict]:
    """
    Извлекает файлы из ZIP архива.
    Track/other/small images -> финальное место.
    PDF/large images -> _work/.

    Args:
        archive_name: имя архива
        zip_path: путь к ZIP
        cache_dir: путь к директории кеша
        write_status_callback: функция для записи статуса (write_prepare_status)

    Returns:
        Список словарей с информацией о файлах:
        [{"zip_path": str, "kind": str, "size": int, "file_ext": str}, ...]
    """
    write_status_callback(archive_name, stage=CACHE_STAGE_EXTRACTING)

    toc = read_zip_toc(zip_path)

    def do_extract():
        work_dir = cache_dir / CACHE_WORK_DIRNAME
        files_info = []
        total = len(toc)

        with zipfile.ZipFile(zip_path, 'r') as zf:
            for count, entry in enumerate(toc, 1):
                decoded_name = entry["zip_path"]
                file_ext = entry["file_ext"]
                kind = entry["kind"]
                file_size = entry["size"]

                # Определяем целевую директорию
                if file_ext == '.pdf':
                    # PDF -> _work/ для конвертации в PNG
                    target_path = work_dir / decoded_name
                elif file_ext in IMAGE_EXTENSIONS:
                    # Все растры -> _work/ для оптимизации (решение ресайз/копия по пикселям)
                    target_path = work_dir / decoded_name
                else:
                    # Track/other -> финальное место
                    target_path = cache_dir / decoded_name

                target_path.parent.mkdir(parents=True, exist_ok=True)

                data = zf.read(entry["original_filename"])
                target_path.write_bytes(data)

                file_info = {
                    "zip_path": decoded_name,
                    "kind": kind,
                    "size": file_size,
                    "file_ext": file_ext,
                }

                # Для tracks/other сразу добавляем cache_path (images — проставит convert_images)
                if kind in ('track', 'other'):
                    file_info["cache_path"] = decoded_name

                files_info.append(file_info)

                write_status_callback(archive_name, stage=CACHE_STAGE_EXTRACTING,
                                      detail=f"Extracted {count}/{total}")

        return files_info

    loop = asyncio.get_event_loop()
    files_info = await loop.run_in_executor(None, do_extract)

    app_logger.debug(f"Files extracted for {archive_name}: {len(files_info)} files")
    return files_info


async def convert_gps_tracks(archive_name: str, zip_path: Path, cache_dir: Path,
                            write_status_callback) -> dict:
    """
    Создает GPS архив со всеми треками.
    
    Args:
        archive_name: имя архива
        zip_path: путь к ZIP
        cache_dir: путь к директории кеша
        write_status_callback: функция для записи статуса (write_prepare_status)
        
    Returns:
        Словарь с информацией о GPS архиве:
        При успехе: {"path": "...-geo.zip", "size": N, "tracks_count": N}
        При ошибке: {"status": "error", "error": "..."}
    """
    write_status_callback(archive_name, stage=CACHE_STAGE_CONVERTING, sub="gps")
    
    from services.archive_service import create_gps_tracks_archive
    
    try:
        success, result = await create_gps_tracks_archive(zip_path, archive_name)
        
        if success:
            geo_path, track_count = result
            geo_size = geo_path.stat().st_size
            app_logger.debug(f"GPS archive created for {archive_name}: {track_count} tracks")
            return {
                "path": f"{archive_name}{GEO_ARCHIVE_SUFFIX}",
                "size": geo_size,
                "tracks_count": track_count
            }
        else:
            app_logger.debug(f"No GPS tracks found for {archive_name}")
            return {"status": CACHE_STATUS_ERROR, "error": "No GPS tracks found"}
    except Exception as e:
        app_logger.warning(f"GPS archive creation failed for {archive_name}: {e}")
        return {"status": CACHE_STATUS_ERROR, "error": str(e)}


async def convert_pdfs(archive_name: str, zip_path: Path, cache_dir: Path, 
                      files_info: list[dict], write_status_callback) -> None:
    """
    Конвертирует PDF файлы в PNG директории.
    Обрабатывает по одному, от мелких к крупным.
    Обновляет files_info с результатами конвертации.
    
    Args:
        archive_name: имя архива
        zip_path: путь к ZIP
        cache_dir: путь к директории кеша
        files_info: список файлов для обновления
        write_status_callback: функция для записи статуса (write_prepare_status)
    """
    from services.conversion.pdf_to_png_service import convert_pdf_to_directory
    
    work_dir = cache_dir / CACHE_WORK_DIRNAME
    
    if not work_dir.exists():
        return
    
    # Собираем все PDF из _work/ (case-insensitive)
    pdfs = [p for p in work_dir.rglob("*") if p.suffix.lower() == ".pdf"]
    
    # Сортируем по размеру (мелкие первые)
    pdfs.sort(key=lambda p: p.stat().st_size)
    
    total_pdfs = len(pdfs)
    
    # Pre-scan: создаём PNG-директории и считаем страницы до начала конвертации.
    # png-viewer может открыть директорию сразу и знать pages_total для заглушек.
    from services.conversion.pdf_to_png_service import count_pdf_pages
    for pdf_path in pdfs:
        rel = str(pdf_path.relative_to(work_dir))
        png_dir_pre = get_png_dir_path(archive_name, rel)
        png_dir_pre.mkdir(parents=True, exist_ok=True)
        pages = count_pdf_pages(pdf_path)
        if pages > 0:
            (png_dir_pre / "_pages_total.txt").write_text(str(pages))
    
    for idx, pdf_path in enumerate(pdfs, start=1):
        try:
            # Получаем относительный путь внутри _work/
            rel_path = pdf_path.relative_to(work_dir)
            rel_path_str = str(rel_path)
            
            # Находим запись в files_info
            file_entry = next((f for f in files_info if f["zip_path"] == rel_path_str), None)
            if not file_entry:
                app_logger.warning(f"PDF entry not found in files_info: {rel_path_str}")
                continue
            
            write_status_callback(
                archive_name,
                stage=CACHE_STAGE_CONVERTING,
                sub="pdf",
                detail=f"PDF {idx}/{total_pdfs}",
                converting_path=rel_path_str
            )
            
            png_dir = get_png_dir_path(archive_name, rel_path_str)
            
            # Callback для постраничного прогресса
            def _page_progress(done, total, _idx=idx, _total=total_pdfs, _rel=rel_path_str):
                pct = int(done / total * 100) if total > 0 else 0
                write_status_callback(
                    archive_name,
                    stage=CACHE_STAGE_CONVERTING,
                    sub="pdf",
                    detail=f"PDF {_idx}/{_total}: {done}/{total} ({pct}%)",
                    pages_total=total,
                    converting_path=_rel
                )
            
            # Конвертируем
            success, result = await convert_pdf_to_directory(pdf_path, png_dir, pdf_path.stem, on_progress=_page_progress)
            
            if not success:
                app_logger.warning(f"PDF conversion failed for {rel_path}: {result}")
                file_entry["status"] = CACHE_STATUS_ERROR
                file_entry["error"] = str(result)
                continue
            
            page_count, total_size = result
            
            # Обновляем file_entry с успешным результатом
            file_entry["png_dir"] = str(Path(rel_path_str).parent / f"{Path(rel_path_str).stem}-png")
            file_entry["pages"] = page_count
            
            # Удаляем temp PDF
            try:
                pdf_path.unlink(missing_ok=True)
            except Exception:
                pass
            
        except Exception as e:
            app_logger.warning(f"Error converting PDF {pdf_path.name}: {e}")
            # Находим запись и помечаем как error
            rel_path = pdf_path.relative_to(work_dir)
            file_entry = next((f for f in files_info if f["zip_path"] == str(rel_path)), None)
            if file_entry:
                file_entry["status"] = CACHE_STATUS_ERROR
                file_entry["error"] = str(e)


async def convert_images(archive_name: str, zip_path: Path, cache_dir: Path, 
                        files_info: list[dict], write_status_callback) -> None:
    """
    Оптимизирует изображения (конвертация в JPG или ресайз в нативном формате).
    PNG оптимизируется как PNG, остальные форматы конвертируются в JPG.
    Обрабатывает по одному, от мелких к крупным.
    Обновляет files_info с результатами оптимизации.
    
    Args:
        archive_name: имя архива
        zip_path: путь к ZIP
        cache_dir: путь к директории кеша
        files_info: список файлов для обновления
        write_status_callback: функция для записи статуса (write_prepare_status)
    """
    from services.conversion.image_conversion_service import optimize_image_sync
    
    work_dir = cache_dir / CACHE_WORK_DIRNAME
    
    if not work_dir.exists():
        return
    
    # Собираем все изображения из _work/ (case-insensitive)
    images = [p for p in work_dir.rglob("*") if p.suffix.lower() in IMAGE_EXTENSIONS]
    
    # Сортируем по размеру (мелкие первые)
    images.sort(key=lambda p: p.stat().st_size)
    
    total_images = len(images)
    
    for idx, img_path in enumerate(images, start=1):
        write_status_callback(
            archive_name,
            stage=CACHE_STAGE_CONVERTING,
            sub="images",
            detail=f"Images {idx}/{total_images}"
        )
        
        try:
            # Получаем относительный путь внутри _work/ (posix-слэши совпадают с zip_path)
            rel_path = img_path.relative_to(work_dir)
            rel_path_str = rel_path.as_posix()
            
            # Находим запись в files_info
            file_entry = next((f for f in files_info if f["zip_path"] == rel_path_str), None)
            if not file_entry:
                app_logger.warning(f"Image entry not found in files_info: {rel_path_str}")
                continue
            
            target_dir = cache_dir / rel_path.parent
            target_dir.mkdir(parents=True, exist_ok=True)
            
            # Определяем выходной путь: PNG остаётся PNG, остальное → .jpg
            file_ext_lower = rel_path.suffix.lower()
            if file_ext_lower in FITZ_NATIVE_IMAGE_FORMATS:
                output_path = target_dir / rel_path.name
                output_ext = file_ext_lower
            else:
                output_path = target_dir / f"{rel_path.stem}.jpg"
                output_ext = ".jpg"
            
            # Проверяем коллизию для целевого пути
            if output_path.exists():
                # Коллизия - перемещаем оригинал с исходным расширением
                orig_path = cache_dir / rel_path
                try:
                    shutil.move(str(img_path), str(orig_path))
                    app_logger.debug(f"Collision detected, moved original: {rel_path_str}")
                    # Обновляем file_entry с оригинальным расширением
                    file_entry["cache_path"] = rel_path_str
                    file_entry["cache_ext"] = file_entry["file_ext"]
                    file_entry["original_ext"] = file_entry["file_ext"]
                except Exception as e:
                    app_logger.warning(f"Failed to move colliding image {rel_path_str}: {e}")
                    file_entry["status"] = CACHE_STATUS_ERROR
                    file_entry["error"] = f"Collision move failed: {e}"
                continue
            
            # Оптимизируем изображение (ресайз или копия — по пиксельным размерам)
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                None,
                optimize_image_sync,
                img_path,
                output_path,
                IMAGE_TO_JPG_MAX_WIDTH,
                IMAGE_TO_JPG_MAX_HEIGHT,
            )
            
            if not success:
                # Ошибка оптимизации - перемещаем оригинал
                orig_path = cache_dir / rel_path
                try:
                    shutil.move(str(img_path), str(orig_path))
                    file_entry["cache_path"] = rel_path_str
                    file_entry["cache_ext"] = file_entry["file_ext"]
                    file_entry["original_ext"] = file_entry["file_ext"]
                except Exception as e:
                    file_entry["status"] = CACHE_STATUS_ERROR
                    file_entry["error"] = f"Optimization failed: {e}"
                continue
            
            # Обновляем file_entry с успешным результатом (posix-слэши)
            output_rel_path = (rel_path.parent / output_path.name).as_posix()
            file_entry["cache_path"] = output_rel_path
            file_entry["cache_ext"] = output_ext
            file_entry["original_ext"] = file_entry["file_ext"]
            
            # Удаляем temp image
            try:
                img_path.unlink(missing_ok=True)
            except Exception:
                pass
            
        except Exception as e:
            app_logger.warning(f"Error converting image {img_path.name}: {e}")
            # Находим запись и помечаем как error
            rel_path = img_path.relative_to(work_dir)
            file_entry = next((f for f in files_info if f["zip_path"] == rel_path.as_posix()), None)
            if file_entry:
                file_entry["status"] = CACHE_STATUS_ERROR
                file_entry["error"] = str(e)


# ============================================================================
# ФУНКЦИИ ЗАПИСИ МЕТАДАННЫХ
# ============================================================================

def _compute_cache_dir_size(cache_dir: Path) -> int:
    """
    Возвращает суммарный размер всех файлов в cache_dir (байты).
    Исключает временные файлы (.tmp-*).

    Args:
        cache_dir: путь к директории кеша архива

    Returns:
        Размер в байтах, 0 при ошибке
    """
    try:
        return sum(
            f.stat().st_size
            for f in cache_dir.rglob('*')
            if f.is_file() and '.tmp-' not in f.name
        )
    except Exception:
        return 0


async def write_meta(archive_name: str, zip_path: Path, cache_dir: Path, 
                    files_info: list[dict], geo_archive_info: dict) -> None:
    """
    Записывает _meta.json с каталогом всех файлов.
    
    Args:
        archive_name: имя архива
        zip_path: путь к ZIP
        cache_dir: путь к директории кеша
        files_info: информация о всех файлах
        geo_archive_info: информация о GPS архиве
    """
    stat = zip_path.stat()
    
    # Вычисляем статистику из files_info
    stats = {
        "total": len(files_info),
        "pdf": 0,
        "images": 0,
        "tracks": 0,
        "other": 0,
        "errors": 0
    }
    
    for file_info in files_info:
        kind = file_info.get("kind", "other")
        
        # Safety net: PDF без png_dir и без status=error -> помечаем как error
        if kind == "pdf" and "png_dir" not in file_info and file_info.get("status") != CACHE_STATUS_ERROR:
            file_info["status"] = CACHE_STATUS_ERROR
            file_info["error"] = "PDF conversion was skipped (no png_dir produced)"
            app_logger.warning(f"PDF without png_dir detected: {file_info.get('zip_path', '?')}")
        
        if file_info.get("status") == CACHE_STATUS_ERROR:
            stats["errors"] += 1
        elif kind == "pdf":
            stats["pdf"] += 1
        elif kind == "image":
            stats["images"] += 1
        elif kind == "track":
            stats["tracks"] += 1
        else:
            stats["other"] += 1
    
    meta = {
        "version": 1,
        "source": {
            "path": str(zip_path),
            "mtime": stat.st_mtime,
            "size": stat.st_size
        },
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "cache_size_bytes": _compute_cache_dir_size(cache_dir),
        "files": files_info,
        "stats": stats
    }
    
    # Добавляем информацию о GPS архиве, если она есть
    if geo_archive_info:
        meta["geo_archive"] = geo_archive_info
    
    meta_path = cache_dir / CACHE_META_FILENAME
    atomic_write_json(meta_path, meta)
    
    app_logger.debug(f"Meta written for {archive_name}: {stats['total']} files, {stats['errors']} errors")


async def write_meta_standalone_pdf(
    archive_name: str,
    pdf_path: Path,
    png_dir: Path,
    pages: int
) -> None:
    """
    Записывает _meta.json для standalone PDF.
    
    Args:
        archive_name: имя архива
        pdf_path: путь к PDF
        png_dir: путь к PNG директории
        pages: количество страниц
    """
    cache_dir = get_cache_dir(archive_name)
    stat = pdf_path.stat()
    
    meta = {
        "version": 1,
        "source": {
            "path": str(pdf_path),
            "mtime": stat.st_mtime,
            "size": stat.st_size
        },
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "cache_size_bytes": _compute_cache_dir_size(cache_dir),
        "files": [
            {
                "zip_path": pdf_path.name,
                "kind": "pdf",
                "png_dir": png_dir.name,
                "pages": pages,
                "size": stat.st_size
            }
        ],
        "stats": {
            "total": 1,
            "pdf": 1,
            "images": 0,
            "tracks": 0,
            "other": 0,
            "errors": 0
        }
    }
    
    meta_path = cache_dir / CACHE_META_FILENAME
    atomic_write_json(meta_path, meta)
    
    app_logger.debug(f"Standalone PDF meta written for {archive_name}: {pages} pages")


async def write_meta_with_error(archive_name: str, pdf_path: Path, error: str) -> None:
    """
    Записывает _meta.json с ошибкой конвертации.
    
    Args:
        archive_name: имя архива
        pdf_path: путь к PDF
        error: текст ошибки
    """
    cache_dir = get_cache_dir(archive_name)
    stat = pdf_path.stat()
    
    meta = {
        "version": 1,
        "source": {
            "path": str(pdf_path),
            "mtime": stat.st_mtime,
            "size": stat.st_size
        },
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "cache_size_bytes": 0,
        "files": [
            {
                "zip_path": pdf_path.name,
                "kind": "pdf",
                "status": CACHE_STATUS_ERROR,
                "error": error
            }
        ],
        "stats": {
            "total": 1,
            "pdf": 1,
            "images": 0,
            "tracks": 0,
            "other": 0,
            "errors": 1
        }
    }
    
    meta_path = cache_dir / CACHE_META_FILENAME
    atomic_write_json(meta_path, meta)
    
    app_logger.debug(f"Error meta written for {archive_name}: {error}")
