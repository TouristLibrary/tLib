# Version 2.4 - 14.05.2026 00:00:00 GMT
# File Watcher Validation - Валидация JSON и ZIP файлов
# v2.4: Ветка «ТипФайла объявлен, но архив отсутствует» в validate_archive_consistency теперь
#       является safety-net для race-conditions. Основная отсечка перенесена в
#       filter_complete_groups (scanner.py): JSON с ТипФайла=zip/pdf остаётся в 20_go/, пока
#       нужный архив не появится в data/. До фикса scanner.py эта ветка срабатывала при
#       разрыве GoogleDrive-синхронизации (JSON приходил раньше ZIP).
# v2.3: BadZipFile-ветка расширена — в сообщение включаются оригинальный текст исключения,
#       размер файла и mtime (ISO-формат). Упрощает диагностику редких случаев, когда
#       stability-window пропустил битый файл (реально повреждённый архив, не-ZIP под .zip).
# Описание: Модуль чистой логики валидации для File Watcher pipeline.
#           - validate_json_file() проверяет JSON на корректность синтаксиса и соответствие схеме assets/schema.json
#           - validate_filename_matches_content() проверяет соответствие имени файла и его содержимого (Шифр, ДопШифр)
#             с учетом нормализации ведущих нулей (12-FRT и 00012-FRT оба валидны для Шифр=12)
#           - validate_json_in_processing() выполняет комплексную усиленную валидацию:
#             1) Проверка кодировки файла (быстрая + детальная при ошибке)
#             2) Валидация JSON синтаксиса и соответствия улучшенной схеме (required, min/max, enum)
#             3) Проверка соответствия имени файла и содержимого
#           - validate_zip_file() проверяет ZIP архивы на Zip Bomb атаки и другие аномалии:
#             1) Размер сжатого файла (MAX_ARCHIVE_SIZE)
#             2) Количество файлов в архиве (MAX_FILES_IN_ARCHIVE)
#             3) Compression ratio - защита от Zip Bomb (MAX_COMPRESSION_RATIO)
#           Все функции - чистые, без побочных эффектов (side effects).

import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from logging_config import app_logger, security_logger
from services.validation.encoding_validation_service import validate_file_encoding, validate_json_encoding_detailed
from services.validation.json_schema_validation_service import validate_json_against_schema


def validate_json_file(json_path: Path) -> Tuple[bool, str]:
    """
    Проверяет JSON на:
    1. Синтаксическую корректность
    2. Соответствие схеме assets/schema.json
    
    Args:
        json_path: Путь к JSON файлу
        
    Returns:
        (is_valid: bool, error_msg: str)
    """
    from config import SCHEMA_PATH
    
    schema_path = Path(SCHEMA_PATH)
    
    if not schema_path.exists():
        error_msg = f"Схема не найдена: {SCHEMA_PATH}"
        app_logger.error(f"[FILE_WATCHER] {error_msg}")
        return False, error_msg
    
    return validate_json_against_schema(json_path, schema_path)


def validate_filename_matches_content(group_id: str, json_file: Path) -> Tuple[bool, str]:
    """
    Проверяет соответствие имени файла и его JSON содержимого.
    
    Правило: Имя файла должно соответствовать полям Шифр и ДопШифр внутри JSON.
    Ведущие нули в Шифре игнорируются при сравнении.
    
    Примеры валидных соответствий:
    - Файл "12-FRT.json" → Шифр=12, ДопШифр="FRT" ✅
    - Файл "00012-FRT.json" → Шифр=12, ДопШифр="FRT" ✅ (ведущие нули игнорируются)
    - Файл "12345.json" → Шифр=12345, ДопШифр="" ✅
    - Файл "00012.json" → Шифр=12, ДопШифр="" ✅
    
    Args:
        group_id: ID группы из имени файла (например "12-FRT" или "00012-FRT")
        json_file: Путь к JSON файлу
        
    Returns:
        (is_valid: bool, error_msg: str)
        - is_valid: True если имя соответствует содержимому
        - error_msg: Пустая строка при успехе или детальное описание несоответствия
    """
    try:
        # Читаем JSON
        with open(json_file, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
        
        # Извлекаем Шифр и ДопШифр из JSON
        json_shifr = data.get("Шифр")
        json_dopshifr = data.get("ДопШифр", "")
        
        # Нормализация: пустая строка и None эквивалентны
        if json_dopshifr is None:
            json_dopshifr = ""
        json_dopshifr = str(json_dopshifr).strip()
        
        # Парсим group_id для извлечения Шифр и ДопШифр из имени файла
        if '-' in group_id:
            file_shifr_str, file_dopshifr = group_id.split('-', 1)
        else:
            file_shifr_str = group_id
            file_dopshifr = ""
        
        # Приводим Шифр к числу для сравнения (убирает ведущие нули)
        try:
            file_shifr_int = int(file_shifr_str)
            json_shifr_int = int(json_shifr)
        except (ValueError, TypeError):
            error_msg = "Невалидный Шифр в файле или JSON"
            return False, error_msg
        
        # Нормализуем ДопШифр для сравнения (регистронезависимо)
        file_dopshifr_norm = file_dopshifr.strip().lower()
        json_dopshifr_norm = json_dopshifr.strip().lower()
        
        # Сравниваем числовые значения Шифра и нормализованные ДопШифр
        # Ведущие нули игнорируются автоматически при int() сравнении
        if file_shifr_int != json_shifr_int or file_dopshifr_norm != json_dopshifr_norm:
            error_msg = (
                f"Имя файла не соответствует содержимому:\n\n"
                f"  Имя файла: {group_id}.json (Шифр={file_shifr_int})\n"
                f"  Содержимое JSON:\n"
                f"    - Шифр = {json_shifr}\n"
                f"    - ДопШифр = '{json_dopshifr}'\n\n"
                f"ПРИМЕЧАНИЕ:\n"
                f"  Ведущие нули игнорируются: 12-FRT и 00012-FRT оба валидны для Шифр=12.\n"
                f"  При копировании в data/ имя будет нормализовано до 5 цифр (00012-FRT)."
            )
            return False, error_msg
        
        return True, ""
        
    except Exception as e:
        error_msg = f"Ошибка проверки соответствия имени и содержимого: {e}"
        app_logger.error(f"[FILE_WATCHER] {error_msg}", exc_info=True)
        return False, error_msg


def validate_archive_consistency(group_id: str) -> Tuple[bool, str]:
    """
    Проверяет две вещи:
      Error A — в группе не более одного архива (.zip/.pdf).
      Error B — ТипФайла в JSON соответствует расширению фактического архива.

    Поиск JSON:
      1. processing/{group_id}.json  (complete / json_only)
      2. data/{normalized}.json      (partial — JSON уже в data/)

    Поиск архива для сверки ТипФайла:
      - Если есть архив в processing/ — он «активный».
      - Иначе — архив в data/ (или None, если архива нет нигде).
        При >1 архиве в data/ сразу возвращается Error A.

    ВАЖНО (safety-net): ветка «active_archive is None при declared != None» теперь
    является страховочной — срабатывает только при редких race-conditions (архив исчез
    между сканом и move_group_to_processing). Основная отсечка — в filter_complete_groups
    (scanner.py): группа с JSON, чей ТипФайла не найден в data/, остаётся в 20_go/ и сюда
    вообще не попадает.

    Возвращает (True, "") если всё ок, иначе (False, текст_ошибки).
    Чистая функция — без побочных эффектов.
    """
    from config import UPLOAD_PROCESSING_DIRECTORY, DATA_DIRECTORY
    from .utils import get_normalized_group_id

    _ARCHIVE_EXTS = {".zip", ".pdf"}
    processing_dir = Path(UPLOAD_PROCESSING_DIRECTORY)
    data_dir = Path(DATA_DIRECTORY)
    normalized = get_normalized_group_id(group_id)

    proc_archives: list[Path] = sorted(
        f for f in processing_dir.glob(f"{group_id}.*")
        if f.suffix.lower() in _ARCHIVE_EXTS
    )

    # Error A: несколько архивов в data.up/20_go/
    if len(proc_archives) > 1:
        exts = ", ".join(a.suffix.lower().lstrip(".") for a in proc_archives)
        return False, (
            f"В группе {group_id} несколько архивов в data.up/20_go/ ({exts}). "
            f"Допускается не более одного архива на карточку."
        )

    # Выбираем источник JSON
    proc_json = processing_dir / f"{group_id}.json"
    data_json = data_dir / f"{normalized}.json"

    if proc_json.exists():
        json_file: Optional[Path] = proc_json
        where = "в data.up/20_go/"
    elif data_json.exists():
        json_file = data_json
        where = "в data/"
    else:
        # JSON нет нигде — проверять нечего (не должно случаться в рабочем pipeline)
        return True, ""

    # Выбираем «активный» архив
    if proc_archives:
        active_archive: Optional[Path] = proc_archives[0]
    else:
        data_archives: list[Path] = sorted(
            f for f in data_dir.glob(f"{normalized}.*")
            if f.suffix.lower() in _ARCHIVE_EXTS
        )
        # Error A: несколько архивов уже в data/
        if len(data_archives) > 1:
            exts = ", ".join(a.suffix.lower().lstrip(".") for a in data_archives)
            return False, (
                f"В data/ для карточки {normalized} обнаружено несколько архивов ({exts}). "
                f"Требуется ручная очистка перед загрузкой новых файлов."
            )
        active_archive = data_archives[0] if data_archives else None

    # Error B: ТипФайла в JSON не совпадает с расширением архива
    try:
        with open(json_file, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception as e:
        return False, f"Не удалось прочитать JSON для сверки ТипФайла: {e}"

    declared = data.get("ТипФайла")
    if isinstance(declared, str):
        declared = declared.strip().lower() or None

    actual = active_archive.suffix.lower().lstrip(".") if active_archive is not None else None

    if declared != actual:
        if active_archive is None:
            return False, (
                f"ТипФайла в JSON = '{declared}', но архив для карточки отсутствует."
            )
        if declared is None:
            return False, (
                f"ТипФайла в JSON = null, но {where} найден архив '{active_archive.name}'. "
                f"Уточните ТипФайла в JSON или удалите архив."
            )
        return False, (
            f"ТипФайла в JSON ('{declared}') не совпадает с расширением архива "
            f"{where} ('{actual}', файл '{active_archive.name}')."
        )

    return True, ""


def validate_json_in_processing(group_id: str) -> Tuple[bool, str]:
    """
    Усиленная валидация JSON (Проверка №1 - УЛУЧШЕННАЯ).
    
    Проверяет:
    1. Кодировку файла (быстрая проверка + детальная при ошибке)
    2. JSON синтаксис
    3. Соответствие улучшенной схеме (required, min/max, enum)
    4. Соответствие имени файла и содержимого (Шифр, ДопШифр)
    
    Заменяет старую Проверку №1 + Проверку №2 (генерация БД удалена).
    
    Args:
        group_id: ID группы
        
    Returns:
        (is_valid: bool, error_msg: str)
    """
    from config import UPLOAD_PROCESSING_DIRECTORY
    
    processing_dir = Path(UPLOAD_PROCESSING_DIRECTORY)
    json_file = processing_dir / f"{group_id}.json"
    
    if not json_file.exists():
        return False, "JSON файл не найден в processing/"
    
    # 1. Быстрая проверка кодировки файла
    is_valid, error_msg = validate_file_encoding(json_file)
    if not is_valid:
        # Детальная проверка для точного сообщения
        try:
            with open(json_file, 'r', encoding='utf-8-sig') as f:
                json_data = json.load(f)
            is_valid_detailed, detailed_error = validate_json_encoding_detailed(json_data)
            if not is_valid_detailed:
                return False, f"Проблема с кодировкой:\n{detailed_error}"
        except:
            pass
        return False, f"Проблема с кодировкой:\n{error_msg}"
    
    # 2. Валидация по улучшенной схеме
    is_valid, error_msg = validate_json_file(json_file)
    if not is_valid:
        return False, error_msg
    
    # 3. Проверка соответствия имени файла и содержимого
    is_valid, error_msg = validate_filename_matches_content(group_id, json_file)
    if not is_valid:
        return False, error_msg

    # 4. Сверка ТипФайла и количества архивов
    is_valid, error_msg = validate_archive_consistency(group_id)
    if not is_valid:
        return False, error_msg

    return True, ""


def validate_zip_file(zip_path: Path) -> Tuple[bool, str]:
    """
    Проверяет ZIP файл на Zip Bomb и другие аномалии.
    
    Проверки:
    1. Размер сжатого файла (MAX_ARCHIVE_SIZE)
    2. Количество файлов в архиве (MAX_FILES_IN_ARCHIVE)
    3. Суммарный размер распакованного содержимого (вычисляется из метаданных)
    4. Compression ratio - защита от Zip Bomb (MAX_COMPRESSION_RATIO)
    5. Корректность ZIP файла (zipfile.BadZipFile)
    
    ВАЖНО: Функция читает только метаданные ZIP (infolist), не распаковывая файлы.
    
    Args:
        zip_path: Путь к ZIP файлу
        
    Returns:
        (is_valid: bool, error_msg: str)
        - is_valid: True если все проверки пройдены
        - error_msg: Пустая строка при успехе или детальное описание ошибки
    """
    from config import MAX_ARCHIVE_SIZE, MAX_FILES_IN_ARCHIVE, MAX_COMPRESSION_RATIO
    
    try:
        # Проверка 1: Размер сжатого файла
        compressed_size = zip_path.stat().st_size
        if compressed_size > MAX_ARCHIVE_SIZE:
            error_msg = (
                f"ZIP слишком большой: {compressed_size / 1024 / 1024:.1f} MB\n"
                f"Максимально допустимый размер: {MAX_ARCHIVE_SIZE / 1024 / 1024:.0f} MB"
            )
            app_logger.warning(f"[FILE_WATCHER] {zip_path.name}: {error_msg}")
            return False, error_msg
        
        # Открываем архив для анализа метаданных (БЕЗ распаковки)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            info_list = zf.infolist()
            
            # Проверка 2: Количество файлов
            if len(info_list) > MAX_FILES_IN_ARCHIVE:
                error_msg = (
                    f"Слишком много файлов в архиве: {len(info_list)}\n"
                    f"Максимально допустимое количество: {MAX_FILES_IN_ARCHIVE}"
                )
                app_logger.warning(f"[FILE_WATCHER] {zip_path.name}: {error_msg}")
                return False, error_msg
            
            # Проверка 3: Суммарный распакованный размер и compression ratio
            total_uncompressed = sum(info.file_size for info in info_list)
            
            # Проверка 4: Compression ratio (защита от Zip Bomb)
            if compressed_size > 0:
                ratio = total_uncompressed / compressed_size
                if ratio > MAX_COMPRESSION_RATIO:
                    error_msg = (
                        f"Подозрительный compression ratio: {ratio:.1f}x "
                        f"(макс {MAX_COMPRESSION_RATIO}x)\n"
                        f"Сжатый размер: {compressed_size / 1024 / 1024:.2f} MB\n"
                        f"Распакованный размер: {total_uncompressed / 1024 / 1024:.2f} MB\n"
                        f"ВОЗМОЖНАЯ ZIP BOMB АТАКА"
                    )
                    
                    # Security event - потенциальная Zip Bomb атака
                    security_logger.log_zip_bomb_detected(
                        filename=zip_path.name,
                        ratio=round(ratio, 1),
                        compressed_mb=round(compressed_size / 1024 / 1024, 2),
                        uncompressed_mb=round(total_uncompressed / 1024 / 1024, 2)
                    )
                    
                    app_logger.warning(f"[FILE_WATCHER] {zip_path.name}: {error_msg}")
                    return False, error_msg
        
        # Все проверки пройдены
        app_logger.debug(
            f"[FILE_WATCHER] ZIP validation passed: {zip_path.name}, "
            f"size={compressed_size / 1024 / 1024:.2f}MB, "
            f"files={len(info_list)}"
        )
        return True, ""
        
    except zipfile.BadZipFile as e:
        st = zip_path.stat()
        error_msg = (
            f"Поврежденный или невалидный ZIP файл: {e} "
            f"(size={st.st_size} bytes, "
            f"mtime={datetime.fromtimestamp(st.st_mtime).isoformat(timespec='seconds')})"
        )
        app_logger.warning(f"[FILE_WATCHER] {zip_path.name}: {error_msg}")
        return False, error_msg
    except Exception as e:
        error_msg = f"Ошибка проверки ZIP: {e}"
        app_logger.error(f"[FILE_WATCHER] {zip_path.name}: {error_msg}", exc_info=True)
        return False, error_msg
