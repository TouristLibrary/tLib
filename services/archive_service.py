# Version 3.0 - 06.02.2026 00:00:00 GMT
# Archive Service для TlibWebApp
# Описание: Бизнес-логика работы с ZIP архивами. Выполняет чтение содержимого архивов,
#           стриминг оригинальных файлов из ZIP и создание GPS-архивов.
#           Извлечение файлов в кеш теперь выполняется в cache_prepare_service.py.
#           Декодирует имена файлов внутри ZIP с поддержкой кириллицы и формирует
#           download URL для клиента. Для скачивания файлов генерирует Content-Disposition
#           с совместимым ASCII fallback и UTF-8 параметром filename* (RFC 5987/6266).
#           Все функции возвращают tuple (success: bool, result) для явной обработки ошибок.

import os
import tempfile
import zipfile
import asyncio
import urllib.parse
import logging
from pathlib import Path
from typing import Tuple, Union, Dict

# Импорт конфигурации
from config import (
    MAX_ARCHIVE_SIZE, MAX_FILE_SIZE, MAX_FILES_IN_ARCHIVE,
    GPS_TRACK_EXTENSIONS, MIN_TRACKS_FOR_ARCHIVE,
    FILTER_MACOS_METADATA, LOCAL_ARCHIVE_PATH,
    INLINE_EXTENSIONS,
    GEO_ARCHIVE_SUFFIX,
    DEFAULT_FILE_EXTENSION
)

# Импорт вспомогательных сервисов
from services.file_service import decode_zip_filename, is_macos_metadata_file
from services.cache.cache_service import get_cache_dir

# Импорт логгеров
from logging_config import app_logger, log_with_data, security_logger


# ============================================================================
# ВАЛИДАЦИЯ БИЗНЕС-ПРАВИЛ
# ============================================================================

def validate_archive_business_rules(
    zip_path: Path, 
    client_ip: str, 
    filename: str
) -> Tuple[bool, Union[Path, Dict]]:
    """
    Проверяет бизнес-правила для архива (существование, размер).
    
    Security validation (Path Traversal, directory checks) выполняется в роутере.
    Эта функция проверяет только бизнес-правила.
    
    Args:
        zip_path: Путь к архиву (уже прошел security validation)
        client_ip: IP адрес клиента для логирования
        filename: Имя архива для логирования
        
    Returns:
        (True, zip_path) - если все проверки пройдены
        (False, {"status_code": int, "message": str}) - если есть ошибка
    """
    # Проверка существования архива
    if not zip_path.exists():
        return False, {"status_code": 404, "message": "Archive not found"}
    
    # БИЗНЕС-ПРАВИЛО: Ограничение размера архива (защита от DoS)
    file_size = zip_path.stat().st_size
    if file_size > MAX_ARCHIVE_SIZE:
        security_logger.log_archive_size_exceeded(client_ip, filename, file_size)
        return False, {"status_code": 413, "message": "Archive too large"}
    
    # Все проверки пройдены успешно
    return True, zip_path


# ============================================================================
# ЧТЕНИЕ СПИСКА ФАЙЛОВ ИЗ АРХИВА
# ============================================================================

async def get_archive_file_list(
    zip_path: Path, 
    filename: str
) -> Tuple[bool, Union[Dict, str]]:
    """
    Читает содержимое ZIP архива и возвращает список файлов.
    
    Выполняет:
    - Фильтрацию macOS metadata (если FILTER_MACOS_METADATA=True)
    - Проверку количества файлов (MAX_FILES_IN_ARCHIVE)
    - Проверку размеров отдельных файлов (MAX_FILE_SIZE)
    - Декодирование имен файлов (decode_zip_filename)
    
    Args:
        zip_path: Путь к архиву
        filename: Имя архива (без .zip) для формирования URLs
        
    Returns:
        (True, dict) - с полями name, files, download_url
        (False, error_message) - при ошибке
    """
    try:
        files = []
        
        # Читаем архив в thread pool чтобы не блокировать event loop
        def read_zip():
            with zipfile.ZipFile(zip_path, 'r') as zip_file:
                # БИЗНЕС-ПРАВИЛО: Ограничиваем количество файлов
                if len(zip_file.infolist()) > MAX_FILES_IN_ARCHIVE:
                    raise ValueError("Too many files in archive")
                
                for info in zip_file.infolist():
                    if not info.is_dir():
                        # БИЗНЕС-ПРАВИЛО: Проверяем размер распакованного файла
                        if info.file_size > MAX_FILE_SIZE:
                            continue  # Пропускаем слишком большие файлы
                        
                        # ФИЛЬТРАЦИЯ: Пропускаем служебные файлы macOS
                        if FILTER_MACOS_METADATA and is_macos_metadata_file(info.filename):
                            continue  # Пропускаем __MACOSX/ и ._ файлы
                        
                        decoded_filename = decode_zip_filename(info.filename)
                        files.append({
                            "name": decoded_filename,
                            "size": info.file_size,
                            "compressed_size": info.compress_size,
                            "download_url": f"/api/archive/{filename}/file/{urllib.parse.quote(decoded_filename, safe='')}"
                        })
            return files
        
        # Выполняем чтение в thread pool
        loop = asyncio.get_event_loop()
        files = await loop.run_in_executor(None, read_zip)
        
        # Формируем ответ
        archive_name = zip_path.stem  # Имя без расширения
        response_data = {
            "name": archive_name,
            "files": files,
            "download_url": f"{LOCAL_ARCHIVE_PATH}/{archive_name}.zip"
        }
        
        return True, response_data
        
    except ValueError as e:
        # Ошибки валидации (слишком много файлов)
        app_logger.warning(f"Validation error for archive {filename}: {e}")
        return False, str(e)
    except Exception as e:
        # Неожиданные ошибки
        app_logger.error(f"Error reading archive {filename}: {e}", exc_info=True)
        return False, "Error reading archive"


# ============================================================================
# СТРИМИНГ ОРИГИНАЛА ИЗ ZIP
# ============================================================================

async def stream_original_from_archive(
    zip_path: Path,
    filepath: str
) -> Tuple[bool, Union[bytes, str]]:
    """
    Извлекает оригинальный файл из архива без кеширования для прямой отдачи.
    
    Используется для открытия оригинальных файлов в новой вкладке
    при клике на "arrow-up-right" (параметр ?original=1).
    
    НЕ кеширует файл - читает напрямую из ZIP и возвращает байты.
    
    Args:
        zip_path: Путь к архиву
        filepath: Путь к файлу внутри архива
        
    Returns:
        (True, file_bytes: bytes) - при успехе, содержимое файла
        (False, error_message: str) - при ошибке
    """
    try:
        # Функция для чтения файла из ZIP
        def read_from_zip():
            with zipfile.ZipFile(zip_path, 'r') as zip_file:
                info = None
                
                # Сначала пробуем прямое чтение
                try:
                    info = zip_file.getinfo(filepath)
                except KeyError:
                    # Если не найден, ищем с учетом кодировки
                    for file_info in zip_file.infolist():
                        if not file_info.is_dir():
                            # БИЗНЕС-ПРАВИЛО: Проверяем размер
                            if file_info.file_size > MAX_FILE_SIZE:
                                continue
                            
                            decoded_name = decode_zip_filename(file_info.filename)
                            if decoded_name == filepath or file_info.filename == filepath:
                                info = file_info
                                break
                
                if info is None:
                    return None, "File not found"
                
                # БИЗНЕС-ПРАВИЛО: Проверяем размер файла
                if info.file_size > MAX_FILE_SIZE:
                    return None, "File too large"
                
                # Читаем файл из архива
                file_data = zip_file.read(info.filename)
                
                return file_data, None
        
        # Выполняем чтение в thread pool
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, read_from_zip)
        
        file_data, error = result
        
        if error:
            return False, error
        
        if file_data is None:
            return False, "File not found"
        
        return True, file_data
        
    except Exception as e:
        # Неожиданные ошибки
        app_logger.error(f"Error streaming original file {filepath}: {e}", exc_info=True)
        return False, "Error reading file"


# ============================================================================
# СОЗДАНИЕ АРХИВА С GPS-ТРЕКАМИ
# ============================================================================

async def create_gps_tracks_archive(
    zip_path: Path, 
    filename: str
) -> Tuple[bool, Union[Tuple[Path, int], str]]:
    """
    Создает ZIP с GPS-треками из архива.
    
    Путь: {cache_dir}/{archive_name}-geo.zip (без версионных хешей).
    
    Выполняет:
    - Поиск файлов с расширениями из GPS_TRACK_EXTENSIONS
    - Проверку минимального количества треков (MIN_TRACKS_FOR_ARCHIVE)
    - Кэширование в CACHE_DIRECTORY
    - Фильтрацию macOS metadata
    
    Args:
        zip_path: Путь к архиву
        filename: Имя архива (без .zip)
        
    Returns:
        (True, (cache_path: Path, track_count: int)) - при успехе
        (False, error_message) - при ошибке
    """
    try:
        # Расширения GPS-треков (из конфигурации)
        track_extensions = GPS_TRACK_EXTENSIONS
        
        # Получаем имя архива из пути
        archive_name = zip_path.stem
        
        # Функция для создания архива с треками
        def create_tracks():
            # Путь к GPS-архиву: {cache_dir}/{archive}-geo.zip
            cache_dir = get_cache_dir(archive_name)
            cache_dir.mkdir(parents=True, exist_ok=True)
            geo_path = cache_dir / f"{archive_name}{GEO_ARCHIVE_SUFFIX}"
            
            # Cache hit
            if geo_path.exists() and geo_path.is_file():
                try:
                    with zipfile.ZipFile(geo_path, "r") as existing_zip:
                        track_count = len([f for f in existing_zip.namelist() if not f.endswith("/")])
                    os.utime(geo_path, None)  # LRU touch
                    app_logger.debug(f"GPS cache hit: {geo_path.name}, {track_count} tracks")
                    return geo_path, track_count
                except Exception:
                    # Битый архив - удаляем и пересоздаем
                    app_logger.warning(f"Corrupted GPS cache: {geo_path.name}, recreating")
                    try:
                        geo_path.unlink(missing_ok=True)
                    except Exception:
                        pass
            
            # Кеш отсутствует/устарел/битый — пересоздаем.
            # Важно: пишем в tmp и затем os.replace, чтобы не отдавать частично записанный ZIP.
            fd, tmp_zip_str = tempfile.mkstemp(
                prefix=f"{archive_name}{GEO_ARCHIVE_SUFFIX.rsplit('.', 1)[0]}.",
                suffix=".zip.tmp",
                dir=str(cache_dir)
            )
            os.close(fd)
            tmp_zip_path = Path(tmp_zip_str)
            
            # Собираем все треки
            tracks = []
            
            with zipfile.ZipFile(zip_path, "r") as source_zip:
                for info in source_zip.infolist():
                    if not info.is_dir():
                        # ФИЛЬТРАЦИЯ: Пропускаем служебные файлы macOS
                        if FILTER_MACOS_METADATA and is_macos_metadata_file(info.filename):
                            continue
                        
                        file_ext = Path(info.filename).suffix.lower()
                        if file_ext in track_extensions:
                            # БИЗНЕС-ПРАВИЛО: Проверяем размер файла
                            if info.file_size <= MAX_FILE_SIZE:
                                decoded_name = decode_zip_filename(info.filename)
                                track_data = source_zip.read(info.filename)
                                tracks.append((decoded_name, track_data))
            
            # БИЗНЕС-ПРАВИЛО: Архив создается только при наличии минимума треков
            if len(tracks) < MIN_TRACKS_FOR_ARCHIVE:
                try:
                    tmp_zip_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return None, 0
            
            # Создаем новый ZIP с треками (во временный файл)
            try:
                with zipfile.ZipFile(tmp_zip_path, "w", zipfile.ZIP_DEFLATED) as tracks_zip:
                    for track_name, track_data in tracks:
                        tracks_zip.writestr(track_name, track_data)

                os.replace(str(tmp_zip_path), str(geo_path))
            finally:
                try:
                    tmp_zip_path.unlink(missing_ok=True)
                except Exception:
                    pass

            log_with_data(logging.INFO, "GPS archive created",
                         tracks=len(tracks),
                         archive=filename)

            return geo_path, len(tracks)
        
        # Выполняем создание архива в thread pool
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, create_tracks)
        
        temp_path, track_count = result
        
        if temp_path is None:
            return False, "No tracks found"
        
        return True, (temp_path, track_count)
        
    except Exception as e:
        app_logger.error(f"Error creating GPS archive for {filename}: {e}", exc_info=True)
        return False, "Error creating GPS archive"


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

def _strip_crlf(value: str) -> str:
    """
    Удаляет CR/LF из строки.
    
    Это защита от header injection при использовании пользовательских строк
    (например, имен файлов из архива) в HTTP заголовках.
    """
    return value.replace("\r", "").replace("\n", "")


def _to_ascii_filename_fallback(filename: str, default_suffix: str) -> str:
    """
    Формирует безопасное ASCII имя для параметра Content-Disposition filename="...".
    
    Важно: не используем encode('ascii','ignore'), чтобы не получать ведущие пробелы
    и «обнуление» кириллицы (например, "Приложение 2.pdf" -> " 2.pdf").
    """
    # 1) Убираем CR/LF и заменяем не-ASCII на подчёркивания
    cleaned = _strip_crlf(filename)
    ascii_only = "".join(ch if 32 <= ord(ch) < 127 else "_" for ch in cleaned)
    
    # 2) Убираем символы, которые могут ломать заголовок
    ascii_only = (
        ascii_only
        .replace('"', "'")
        .replace("\\", "_")
        .replace(";", "_")
    )
    
    # 3) Тримминг пробелов и «windows-опасных» хвостов
    ascii_only = ascii_only.strip().rstrip(" .")
    
    if not ascii_only:
        suffix = default_suffix or DEFAULT_FILE_EXTENSION
        return "file" + suffix
    
    return ascii_only


def determine_content_disposition(filepath: str) -> Tuple[str, str]:
    """
    Определяет Content-Disposition для файла.
    
    Проверяет:
    - Расширение файла на принадлежность к INLINE_EXTENSIONS
    - Генерирует совместимое имя файла (ASCII fallback + UTF-8 filename*)
    
    Args:
        filepath: Путь к файлу
        
    Returns:
        (disposition: str, safe_filename: str)
        disposition - "inline" или "attachment" с параметрами filename/filename*
        safe_filename - ASCII fallback (для параметра filename="...")
    """
    # Определяем расширение файла
    ext = Path(filepath).suffix.lower()
    
    # В Content-Disposition передаем только имя файла (без директорий внутри архива)
    original_name = _strip_crlf(Path(filepath).name)
    
    # Формируем ASCII fallback для старых клиентов
    safe_filename = _to_ascii_filename_fallback(original_name, Path(filepath).suffix or DEFAULT_FILE_EXTENSION)
    
    # Формируем UTF-8 имя по RFC 5987/6266 (percent-encoded)
    utf8_quoted = urllib.parse.quote(original_name, safe="")
    
    # Определяем Content-Disposition в зависимости от типа файла
    # Параметр filename* нужен, чтобы браузер корректно показывал кириллицу при сохранении.
    if ext in INLINE_EXTENSIONS:
        disposition = f'inline; filename="{safe_filename}"; filename*=UTF-8\'\'{utf8_quoted}'
    else:
        disposition = f'attachment; filename="{safe_filename}"; filename*=UTF-8\'\'{utf8_quoted}'
    
    return disposition, safe_filename
