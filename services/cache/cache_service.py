# Version 2.0 - 06.02.2026 00:00:00 GMT
# Cache Service для TlibWebApp
# Описание: Централизованный сервис кеширования без версионных хешей.
#           Предоставляет единую логику путей и очистки LRU по целым папкам архивов.
#           Функции чтения кеша (_meta.json, _prepare.json) вызываются из cache_router и cache_prepare_service.

import os
import json
import shutil
from pathlib import Path
from typing import Optional

# Импорт конфигурации
from config import (
    CACHE_DIRECTORY,
    MAX_CACHE_SIZE,
    CACHE_META_FILENAME,
    MTIME_TOLERANCE,
    CACHE_LOCK_DIRNAME
)

# Импорт логгеров
from logging_config import app_logger


# ============================================================================
# ФУНКЦИИ ПУТЕЙ КЕША
# ============================================================================

def get_cache_dir(archive_name: str) -> Path:
    """
    Возвращает путь к директории кеша архива (без создания).
    
    Формат: data.cache/{archive_name}/
    
    Args:
        archive_name: имя архива без расширения
        
    Returns:
        Path к директории кеша
    """
    # Санитизация имени папки (оставляем только буквы, цифры, дефисы и подчёркивания)
    safe_name = "".join(
        c if c.isalnum() or c in "-_" else "_" 
        for c in archive_name
    )
    return Path(CACHE_DIRECTORY) / safe_name


def get_png_dir_path(archive_name: str, pdf_zip_path: str) -> Path:
    """
    Предсказуемый путь к PNG директории для PDF (без хешей).
    
    Формат: data.cache/{archive_name}/{parent}/{stem}-png/
    Пример: data.cache/00001-TST/dir1/report-png/
    
    Args:
        archive_name: имя архива
        pdf_zip_path: путь к PDF внутри архива (может содержать директории)
        
    Returns:
        Path к PNG директории
    """
    p = Path(pdf_zip_path)
    return get_cache_dir(archive_name) / p.parent / f"{p.stem}-png"


def get_meta_path(archive_name: str) -> Path:
    """
    Возвращает путь к _meta.json для архива.
    
    Args:
        archive_name: имя архива
        
    Returns:
        Path к _meta.json
    """
    return get_cache_dir(archive_name) / CACHE_META_FILENAME


def read_meta(archive_name: str) -> Optional[dict]:
    """
    Читает _meta.json; None если нет/битый.
    
    ЕДИНСТВЕННОЕ определение read_meta() во всём проекте.
    Используется из cache_router (resolve) и cache_prepare_service (is_cache_valid).
    
    Args:
        archive_name: имя архива
        
    Returns:
        dict с данными meta или None
    """
    meta_path = get_meta_path(archive_name)
    
    try:
        if not meta_path.exists():
            return None
        
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        
        return data
    except Exception:
        return None


def is_cache_valid_from_meta(meta: dict) -> bool:
    """
    Проверяет актуальность кеша по _meta.json.
    Сравнивает source.mtime/size с текущим файлом на диске.
    
    Args:
        meta: словарь с данными из _meta.json
        
    Returns:
        True если кеш актуален или source удален, False если устарел
    """
    try:
        source_path = Path(meta["source"]["path"])
        expected_mtime = meta["source"]["mtime"]
        expected_size = meta["source"]["size"]
        
        if not source_path.exists():
            return True
        
        stat = source_path.stat()
        actual_mtime = stat.st_mtime
        actual_size = stat.st_size
        
        # Сравниваем с небольшой погрешностью для mtime (файловые системы могут округлять)
        mtime_match = abs(actual_mtime - expected_mtime) < MTIME_TOLERANCE
        size_match = actual_size == expected_size
        
        return mtime_match and size_match
    except (KeyError, FileNotFoundError, Exception):
        return False


def is_cache_valid(archive_name: str, source_path: Path) -> bool:
    """
    Проверяет актуальность кеша.
    Читает _meta.json и вызывает is_cache_valid_from_meta().
    
    Args:
        archive_name: имя архива
        source_path: путь к исходному файлу (ZIP или PDF)
        
    Returns:
        True если кеш актуален, False если устарел или отсутствует
    """
    meta = read_meta(archive_name)
    if meta is None:
        return False
    
    return is_cache_valid_from_meta(meta)


# ============================================================================
# LRU ОЧИСТКА КЕША
# ============================================================================

def ensure_cache_space(required_size: int) -> None:
    """
    Освобождает место в кеше для нового файла если необходимо.
    
    Использует стратегию LRU по целым папкам архивов (не по отдельным файлам).
    Сортировка по mtime папки, удаление через shutil.rmtree().
    ПРОПУСКАЕТ папки с _prepare.lockdir внутри (активная подготовка).
    
    Args:
        required_size: Размер файла который нужно добавить в кеш (в байтах)
    """
    cache_dir = Path(CACHE_DIRECTORY)
    
    # Создаем директорию если её нет
    if not cache_dir.exists():
        cache_dir.mkdir(exist_ok=True)
        return
    
    # Собираем все папки архивов с размером и mtime
    folders = []
    total_size = 0
    
    for folder_path in cache_dir.iterdir():
        if not folder_path.is_dir():
            continue
        
        # Проверяем наличие lock
        lock_dir = folder_path / CACHE_LOCK_DIRNAME
        if lock_dir.exists():
            # Активная подготовка - пропускаем
            continue
        
        try:
            # Быстрый путь: читаем cache_size_bytes из _meta.json
            folder_size = None
            meta_path = folder_path / CACHE_META_FILENAME
            try:
                meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
                folder_size = meta_data.get("cache_size_bytes")
            except Exception:
                pass

            # Fallback: обходим файлы напрямую (старый кеш без cache_size_bytes)
            if folder_size is None:
                folder_size = sum(
                    f.stat().st_size
                    for f in folder_path.rglob('*')
                    if f.is_file() and '.tmp-' not in f.name
                )

            folder_mtime = folder_path.stat().st_mtime
            folders.append((folder_path, folder_size, folder_mtime))
            total_size += folder_size
        except Exception as e:
            app_logger.warning(f"Error reading cache folder {folder_path.name}: {e}")
            continue
    
    # Проверяем нужна ли очистка
    if total_size + required_size <= MAX_CACHE_SIZE:
        return
    
    app_logger.info(
        f"Cache cleanup needed: current {total_size / 1024 / 1024:.2f} MB + "
        f"required {required_size / 1024 / 1024:.2f} MB > "
        f"limit {MAX_CACHE_SIZE / 1024 / 1024:.2f} MB"
    )
    
    # Сортируем по mtime (старые первые = LRU)
    folders.sort(key=lambda x: x[2])
    
    deleted_count = 0
    freed_size = 0
    
    # Удаляем старые папки пока не освободим достаточно места
    for folder_path, folder_size, folder_mtime in folders:
        # Проверяем достигли ли цели
        if total_size + required_size <= MAX_CACHE_SIZE:
            break
        
        try:
            shutil.rmtree(folder_path)
            total_size -= folder_size
            freed_size += folder_size
            deleted_count += 1
            
            app_logger.info(f"Cache cleanup: deleted folder {folder_path.name}")
            
        except Exception as e:
            app_logger.warning(f"Failed to delete cache folder {folder_path.name}: {e}")
            continue
    
    if deleted_count > 0:
        app_logger.info(
            f"Cache cleanup completed: deleted {deleted_count} folders, "
            f"freed {freed_size / 1024 / 1024:.2f} MB"
        )


# ============================================================================
# ИНВАЛИДАЦИЯ КЕША
# ============================================================================

def invalidate_archive_cache(archive_name: str) -> int:
    """
    Удаляет весь кеш для архива (всю папку).
    
    Args:
        archive_name: имя архива без расширения
        
    Returns:
        Количество удалённых файлов
    """
    cache_dir = get_cache_dir(archive_name)
    
    if not cache_dir.exists():
        return 0
    
    # Считаем файлы перед удалением
    deleted = sum(1 for f in cache_dir.rglob('*') if f.is_file())
    
    # Удаляем всю папку
    try:
        shutil.rmtree(cache_dir)
        app_logger.info(f"Invalidated cache folder: {cache_dir.name} ({deleted} files)")
    except Exception as e:
        app_logger.warning(f"Failed to invalidate cache folder {cache_dir.name}: {e}")
        return 0
    
    return deleted


# ============================================================================
# УТИЛИТЫ
# ============================================================================

def atomic_write_json(path: Path, data: dict) -> None:
    """
    Атомарно пишет JSON (tmp + os.replace).
    
    Args:
        path: путь к целевому файлу
        data: данные для записи
    """
    tmp_path = Path(f"{path}.tmp-{os.getpid()}")
    try:
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        os.replace(str(tmp_path), str(path))
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
