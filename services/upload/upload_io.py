# Version 1.1 - 21.06.2026 21:00:00 GMT
# Upload IO helpers для TlibWebApp
# Описание: Потоковая запись загружаемых файлов на диск (защита RAM от исчерпания)
#           и проверка достаточности свободного места перед приёмом файлов.
#           stream_upload_to_temp() — чанковая запись UploadFile во временный файл
#           с ранним обрывом при превышении лимита размера.
#           get_disk_usage() — единый helper расчёта метрик диска;
#           переиспользуется в services/alerts/digest.py.
#           disk_allows_upload() — проверка двух условий (% занятости + абсолютный резерв)
#           перед тем, как начать принимать файл от пользователя.
# 1.1: fail-open возвращает used_pct=None, free_gb=None (честно «неизвестно»
#      вместо 0/0.0) при недоступности shutil.disk_usage.

import shutil
import tempfile
from pathlib import Path

from config import (
    DISK_CRIT_PERCENT,
    MAX_ARCHIVE_SIZE,
    UPLOAD_DISK_RESERVE_MULTIPLIER,
    UPLOAD_READ_CHUNK_SIZE,
)
from logging_config import app_logger


class UploadTooLargeError(Exception):
    """Файл превысил максимально допустимый размер при потоковой записи."""


async def stream_upload_to_temp(
    file,
    dest_dir: Path,
    max_bytes: int,
    chunk: int = UPLOAD_READ_CHUNK_SIZE,
) -> tuple[Path, int]:
    """Записывает UploadFile чанками во временный файл внутри dest_dir.

    Останавливается и поднимает UploadTooLargeError, если суммарный размер
    превышает max_bytes. Незаконченный temp-файл при этом удаляется.
    Temp размещается в dest_dir, поэтому финальный shutil.move() — атомарный
    rename на том же томе (нет межтомного копирования).

    Args:
        file:      FastAPI UploadFile.
        dest_dir:  Директория для temp-файла (обычно UPLOAD_STAGING_DIRECTORY).
        max_bytes: Жёсткий лимит размера в байтах (обычно MAX_ARCHIVE_SIZE).
        chunk:     Размер чанка чтения в байтах.

    Returns:
        (tmp_path, total_size) — путь к готовому temp-файлу и его размер в байтах.

    Raises:
        UploadTooLargeError: если суммарный размер превысил max_bytes.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    tmp_fd = tempfile.NamedTemporaryFile(
        delete=False,
        dir=dest_dir,
        prefix="_tmp_upload_",
    )
    tmp_path = Path(tmp_fd.name)
    total = 0
    try:
        while True:
            data = await file.read(chunk)
            if not data:
                break
            total += len(data)
            if total > max_bytes:
                tmp_fd.close()
                tmp_path.unlink(missing_ok=True)
                raise UploadTooLargeError(
                    f"Файл превысил допустимый размер {max_bytes} байт"
                )
            tmp_fd.write(data)
        tmp_fd.close()
    except UploadTooLargeError:
        raise
    except Exception:
        try:
            tmp_fd.close()
        except Exception:
            pass
        tmp_path.unlink(missing_ok=True)
        raise

    return tmp_path, total


def get_disk_usage(path: Path | str) -> dict:
    """Возвращает метрики занятости диска для указанного пути.

    Используется как upload_io, так и services/alerts/digest.py —
    единственная точка вызова shutil.disk_usage в обеих подсистемах.

    Returns:
        dict с ключами: total_bytes, used_bytes, free_bytes,
                        used_pct (int, 0–100), free_gb, used_gb, total_gb.
    """
    usage = shutil.disk_usage(str(path))
    total = usage.total
    used = usage.used
    free = usage.free
    used_pct = int(used / total * 100) if total else 0
    gb = 1024 ** 3
    return {
        "total_bytes": total,
        "used_bytes": used,
        "free_bytes": free,
        "used_pct": used_pct,
        "free_gb": round(free / gb, 1),
        "used_gb": round(used / gb, 1),
        "total_gb": round(total / gb, 1),
    }


def disk_allows_upload(path: Path | str) -> tuple[bool, dict]:
    """Проверяет, достаточно ли места для приёма файла перед стримингом.

    Блокирует загрузку если выполняется хотя бы одно условие:
    - занятость диска >= DISK_CRIT_PERCENT (90%), или
    - свободно < MAX_ARCHIVE_SIZE * UPLOAD_DISK_RESERVE_MULTIPLIER (4 ГБ по умолчанию).

    При ошибке чтения диска — fail-open (True): не блокировать ложно.

    Args:
        path: Любой путь на проверяемом томе (обычно UPLOAD_STAGING_DIRECTORY).

    Returns:
        (allowed, info) — разрешена ли загрузка;
        info содержит: reason (str|None), used_pct (int), free_gb (float).
    """
    try:
        usage = get_disk_usage(path)
    except Exception as e:
        app_logger.error(f"[upload_io] Ошибка проверки диска {path}: {e}")
        return True, {"reason": None, "used_pct": None, "free_gb": None}

    reserve_bytes = MAX_ARCHIVE_SIZE * UPLOAD_DISK_RESERVE_MULTIPLIER
    info: dict = {
        "used_pct": usage["used_pct"],
        "free_gb": usage["free_gb"],
    }

    if usage["used_pct"] >= DISK_CRIT_PERCENT:
        info["reason"] = "disk_critical"
        return False, info

    if usage["free_bytes"] < reserve_bytes:
        info["reason"] = "disk_reserve"
        return False, info

    info["reason"] = None
    return True, info
