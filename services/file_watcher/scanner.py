# Version 1.3 - 14.05.2026 00:00:00 GMT
# File Watcher Scanner - Сканирование и группировка файлов
# Описание: Этап 1 pipeline - сканирование data.up/20_go/ и группировка файлов по ID.
#           - scan_new_files() сканирует data.up/20_go/ и возвращает список файлов с разрешенными расширениями
#           - parse_filename() парсит имя файла по паттернам (ШИФР-ДОПШИФР.ext или ШИФР.ext)
#           - group_files_by_id() группирует файлы по нормализованному ID (Шифр к 5 цифрам, ДопШифр к UPPERCASE),
#               автоматически склеивая разные написания (1-tst.json + 00001-TST.zip → группа 00001-TST)
#           - filter_ambiguous_groups() выявляет группы с дублирующимися именами после нормализации
#               (например, 1-TST.json + 00001-TST.json → один таргет → группа ambiguous, пропускается)
#           - filter_complete_groups() разделяет на 3 категории:
#               complete       — JSON + (PDF или ZIP): полная замена, бэкап всех файлов группы
#               json_only      — только JSON: обновление метаданных, бэкап только .json
#               partial        — PDF/ZIP без JSON, при наличии JSON в data/: бэкап только по расширению
#               ожидает архив  — только JSON с ТипФайла=zip/pdf, но нужного архива нет в data/;
#                                файлы остаются в 20_go/ до прихода архива (защита от разрыва
#                                GoogleDrive-синхронизации между JSON и ZIP/PDF)

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from logging_config import app_logger
from .utils import get_normalized_group_id, normalize_filename_for_data


def scan_new_files() -> List[Path]:
    """
    Сканирует data.up/20_go/ (точку входа pipeline, не processing/error/).
    
    Returns:
        Список Path объектов для файлов с разрешенными расширениями
    """
    from config import UPLOAD_GO_DIRECTORY, ALLOWED_FILE_EXTENSIONS
    
    upload_dir = Path(UPLOAD_GO_DIRECTORY)
    
    if not upload_dir.exists():
        return []
    
    # Только файлы в data.up/20_go/, не в поддиректориях
    all_files = [f for f in upload_dir.iterdir() if f.is_file()]
    
    # Фильтруем по разрешенным расширениям
    allowed_files = [
        f for f in all_files 
        if f.suffix.lower() in ALLOWED_FILE_EXTENSIONS
    ]
    
    return allowed_files


def parse_filename(filename: str) -> Optional[Dict[str, str]]:
    """
    Парсит имя файла по паттернам.
    
    Args:
        filename: Имя файла (например "12345-а.json" или "12345.pdf")
    
    Returns:
        {
            "shifr": "12345",
            "dopshifr": "а" | None,
            "ext": ".json",
            "id": "12345-а" | "12345"
        }
        или None если не совпадает с паттерном
    """
    from config import FILENAME_PATTERN_WITH_DOP, FILENAME_PATTERN_WITHOUT_DOP
    
    # Пробуем паттерн с допшифром
    match = re.match(FILENAME_PATTERN_WITH_DOP, filename, re.IGNORECASE)
    if match:
        shifr, dopshifr, ext = match.groups()
        return {
            "shifr": shifr,
            "dopshifr": dopshifr,
            "ext": f".{ext}",
            "id": f"{shifr}-{dopshifr}"
        }
    
    # Пробуем паттерн без допшифра
    match = re.match(FILENAME_PATTERN_WITHOUT_DOP, filename, re.IGNORECASE)
    if match:
        shifr, ext = match.groups()
        return {
            "shifr": shifr,
            "dopshifr": None,
            "ext": f".{ext}",
            "id": shifr
        }
    
    # Проверка на .delete триггер
    from config import FILENAME_PATTERN_DELETE
    match = re.match(FILENAME_PATTERN_DELETE, filename, re.IGNORECASE)
    if match:
        shifr = match.group(1)
        dopshifr = match.group(2)
        file_id = f"{shifr}-{dopshifr}" if dopshifr else shifr
        return {
            "shifr": shifr,
            "dopshifr": dopshifr,
            "ext": ".delete",
            "id": file_id,
            "operation": "delete"  # Маркер операции удаления
        }
    
    return None


def group_files_by_id(files: List[Path]) -> Dict[str, List[Path]]:
    """
    Группирует файлы по нормализованному ID (Шифр → 5 цифр, ДопШифр → UPPERCASE).

    Разные написания одной карточки автоматически склеиваются в одну группу:
      1-tst.json + 00001-TST.zip → группа "00001-TST"

    Args:
        files: Список файлов

    Returns:
        {
            "00001-TST": [Path("1-tst.json"), Path("00001-TST.zip")],
            "00067-А": [Path("67-а.json"), Path("00067-А.pdf")]
        }
        Ключи всегда в нормализованной форме; значения — оригинальные пути.
    """
    groups: Dict[str, List[Path]] = {}

    for file_path in files:
        parsed = parse_filename(file_path.name)

        if parsed:
            normalized_id = get_normalized_group_id(parsed["id"])
            if normalized_id not in groups:
                groups[normalized_id] = []
            groups[normalized_id].append(file_path)
        else:
            app_logger.warning(
                f"[FILE_WATCHER] Файл не соответствует паттерну: {file_path.name}"
            )

    return groups


def filter_ambiguous_groups(
    groups: Dict[str, List[Path]]
) -> Tuple[Dict[str, List[Path]], Dict[str, List[Path]]]:
    """
    Разделяет группы на чистые и неоднозначные (ambiguous).

    Группа считается ambiguous, если после нормализации имён (normalize_filename_for_data)
    внутри неё обнаруживается два или более исходных файла с одинаковым целевым именем.
    Типичный случай: "1-TST.json" и "00001-TST.json" — оба нормализуются в "00001-TST.json".

    Ambiguous-группы пропускаются pipeline; файлы остаются в data.up/20_go/ до ручного
    вмешательства оператора.

    Args:
        groups: Словарь {normalized_group_id: [original_paths]}

    Returns:
        Tuple[clean_groups, ambiguous_groups]
        - clean_groups:    группы без конфликтов — передаются в дальнейшую обработку
        - ambiguous_groups: группы с конфликтами — {group_id: {target_name: [src_paths]}}
          Для логирования ambiguous_groups хранит детальную картину коллизий.
    """
    clean: Dict[str, List[Path]] = {}
    ambiguous: Dict[str, Dict[str, List[Path]]] = {}

    for group_id, files in groups.items():
        target_map: Dict[str, List[Path]] = {}
        for file_path in files:
            target_name = normalize_filename_for_data(file_path.name)
            if target_name not in target_map:
                target_map[target_name] = []
            target_map[target_name].append(file_path)

        collisions = {t: srcs for t, srcs in target_map.items() if len(srcs) > 1}
        if collisions:
            ambiguous[group_id] = collisions
        else:
            clean[group_id] = files

    return clean, ambiguous


def _peek_declared_filetype(json_path: Path) -> Optional[str]:
    """
    Возвращает 'zip' / 'pdf', если ТипФайла в JSON объявляет архив, иначе None.
    Любая ошибка чтения/парсинга → None: пусть pipeline-валидация ловит сама,
    иначе битый JSON застрянет в 20_go/ навсегда.
    """
    try:
        with open(json_path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:
        return None
    declared = data.get("ТипФайла")
    if isinstance(declared, str):
        declared = declared.strip().lower() or None
    return declared if declared in {"zip", "pdf"} else None


def filter_complete_groups(
    groups: Dict[str, List[Path]]
) -> Tuple[Dict[str, List[Path]], Dict[str, List[Path]], Dict[str, List[Path]]]:
    """
    Разделяет группы на три категории.
    Проверка JSON в data/ выполняется по нормализованному имени (5 цифр).

    Категории:
        complete   — JSON + (PDF или ZIP): полная замена отчёта.
                     Бэкапятся ВСЕ старые файлы группы из data/. Кэш инвалидируется.
        json_only  — только JSON, без PDF/ZIP: обновление метаданных.
                     Бэкапится только старый .json. PDF/ZIP и кэш не затрагиваются.
        partial    — PDF/ZIP без JSON, при наличии JSON в data/: обновление архива.
                     Бэкапятся только файлы с совпадающим расширением. Кэш инвалидируется.

    Args:
        groups: Словарь групп файлов

    Returns:
        Tuple[Dict, Dict, Dict]: (complete_groups, json_only_groups, partial_groups)
        - complete_groups:  JSON + отчёт (требуют генерации БД, бэкап всех файлов)
        - json_only_groups: только JSON (требуют генерации БД, бэкап только .json)
        - partial_groups:   PDF/ZIP без JSON (не требуют генерации БД, бэкап по расширению)
    """
    from config import DATA_DIRECTORY
    from .utils import get_normalized_group_id

    complete_groups = {}   # JSON + (PDF или ZIP)
    json_only_groups = {}  # Только JSON
    partial_groups = {}    # PDF/ZIP без JSON, но JSON есть в data/
    data_dir = Path(DATA_DIRECTORY)

    for group_id, files in groups.items():
        has_json = any(f.suffix.lower() == '.json' for f in files)
        has_report = any(f.suffix.lower() in {'.pdf', '.zip'} for f in files)

        if has_json and has_report:
            # Полная группа: JSON + архив
            complete_groups[group_id] = files
        elif has_json:
            # Только JSON — проверяем, не ждёт ли группа архив
            json_file = next(f for f in files if f.suffix.lower() == ".json")
            declared = _peek_declared_filetype(json_file)

            if declared is not None:
                # ТипФайла объявлен — ждём именно этот тип архива в data/
                expected_suffix = f".{declared}"
                # glob + suffix.lower() — устойчивость к регистру расширения (как в
                # check_data_conflicts / validate_archive_consistency)
                expected_exists = any(
                    f.suffix.lower() == expected_suffix
                    for f in data_dir.glob(f"{group_id}.*")
                )
                if not expected_exists:
                    app_logger.info(
                        f"[FILE_WATCHER] Группа {group_id} ожидает архив "
                        f"(ТипФайла={declared!r}, файла "
                        f"{group_id}{expected_suffix} нет в data/)"
                    )
                    continue

            # Архив не нужен (ТипФайла=null / нет поля) или уже есть в data/
            json_only_groups[group_id] = files
            app_logger.info(
                f"[FILE_WATCHER] Только JSON {group_id}: "
                f"обновление метаданных, архив не затрагивается"
            )
        else:
            # Нет JSON — частичное обновление, если JSON уже есть в data/
            normalized_id = get_normalized_group_id(group_id)
            existing_json = data_dir / f"{normalized_id}.json"

            if existing_json.exists():
                partial_groups[group_id] = files
                app_logger.info(
                    f"[FILE_WATCHER] Частичное обновление {group_id} → {normalized_id} "
                    f"(JSON существует): {[f.name for f in files]}"
                )
            else:
                # JSON нет нигде — пропускаем
                app_logger.info(
                    f"[FILE_WATCHER] Группа {group_id} неполная (нет JSON): "
                    f"{[f.name for f in files]}"
                )

    return complete_groups, json_only_groups, partial_groups
