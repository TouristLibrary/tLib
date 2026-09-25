# Version 1.2 - 25.09.2026 12:40:00 GMT
# Cache Watch для TlibWebApp
# Описание: Heartbeat просмотра PNG-директории и её частичное состояние.
#           png-viewer раз в 2 с опрашивает /api/png/.../pages — роутер отмечает это в _watch.json
#           (время и страница, которую показывает вьюер). Конвертер PDF→PNG читает heartbeat
#           между страницами: рендерит запрошенную страницу первой и встаёт на паузу, когда
#           смотреть перестали. Частичная директория (PNG меньше, чем в _pages_total.txt)
#           докручивается при следующем просмотре.
# 1.1: touch_watch обновляет mtime папки архива — LRU видит просмотр PDF
#      (PDF-вьюер не ходит в /resolve, где метку обновляют image/track).
# 1.2: heartbeat и LRU-метка в отдельных try — сбой одного не маскируется сообщением другого.

import os
import time
from pathlib import Path
from typing import Optional, Tuple

# Импорт конфигурации
from config import PNG_WATCH_FILENAME, PNG_PAGES_TOTAL_FILENAME

# Импорт логгеров
from logging_config import app_logger

# Импорт вспомогательных сервисов
from services.json_io import read_json
from .cache_service import atomic_write_json


def touch_watch(png_dir: Path, want: Optional[int], archive_dir: Path) -> None:
    """
    Отмечает, что директорию сейчас смотрят.
    Ошибка записи не должна ломать листинг страниц — только warning в лог.

    Args:
        png_dir: PNG-директория
        want: страница (1-based), которую показывает вьюер; None — не указана
        archive_dir: папка архива в кеше (единица LRU-вытеснения)
    """
    try:
        atomic_write_json(png_dir / PNG_WATCH_FILENAME, {"ts": time.time(), "want": want})
    except Exception as e:
        app_logger.warning(f"Failed to write watch heartbeat for {png_dir.name}: {e}")
    # ensure_cache_space вытесняет папки архивов по mtime: без этого читаемый PDF
    # выглядел бы «старым» — PDF-вьюер не ходит в /resolve, где метку обновляют image/track
    try:
        os.utime(archive_dir, None)
    except Exception as e:
        app_logger.warning(f"Failed to touch archive dir {archive_dir.name} for LRU: {e}")


def read_watch(png_dir: Path) -> Tuple[float, Optional[int]]:
    """
    Читает последний heartbeat просмотра.

    Args:
        png_dir: PNG-директория

    Returns:
        (ts, want): unix-время heartbeat и запрошенная страница (1-based);
        (0.0, None), если heartbeat нет или файл битый
    """
    try:
        data = read_json(png_dir / PNG_WATCH_FILENAME)
        ts = float(data.get("ts") or 0)
        want = data.get("want")
        if not isinstance(want, int) or want < 1:
            want = None
        return ts, want
    except Exception:
        return 0.0, None


def read_pages_total(png_dir: Path) -> Optional[int]:
    """
    Число страниц PDF из маркера pre-scan.

    Returns:
        Число страниц или None, если маркера нет или он битый
    """
    try:
        return int((png_dir / PNG_PAGES_TOTAL_FILENAME).read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def count_pngs(png_dir: Path) -> int:
    """Число готовых PNG-страниц в директории."""
    return sum(1 for _ in png_dir.glob("*.png"))


def is_partial(png_dir: Path) -> bool:
    """
    True, если конвертация PDF дошла не до конца: PNG меньше, чем страниц в маркере pre-scan.
    Директория без маркера (старый кеш) частичной не считается.
    """
    pages_total = read_pages_total(png_dir)
    return pages_total is not None and count_pngs(png_dir) < pages_total
