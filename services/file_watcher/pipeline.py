# Version 2.7 - 26.07.2026 09:00:00 GMT
# v2.7: canonicalize_json_dopshifr — приведение поля ДопШифр в JSON к UPPERCASE в 30_processing/.
# File Watcher Pipeline - Оркестрация staged pipeline
#
# Текущая функциональность:
# Модуль оркестрации File Watcher staged pipeline. Содержит process_upload_cycle() и внутренние шаги пайплайна.
# Пайплайн обработки: data.up/20_go/ → (stability-window) → 30_processing/ → (валидация) → data/ или 40_error/ → done/
#
# v2.5 (14.05.2026): DELETE переведён на единый канал записи в БД.
# process_delete_operation() теперь только переносит файлы из data/ в data.old/.
# Пересборка tlib-new.db и refresh app.state выполняются через тот же путь, что INSERT/UPDATE:
#   _process_delete_operations → generate_final_database_check([]) → publish_database()
#   → database_watcher_task → perform_database_update (swap + refresh app.state).
# Инвариант: data/ — единственный источник истины для БД.
#
# v2.6 (14.05.2026): Вариант Б — нормализация ID на этапе scan (Шифр → 5 цифр,
# ДопШифр → UPPERCASE). Группировка ведётся по нормализованному ID, разные написания
# одной карточки (1-tst.json + 00001-TST.zip) склеиваются в одну complete-группу.
# Добавлен детект ambiguous-групп (дубликаты после нормализации → SKIP + WARNING).
# Файлы переименовываются в каноническую форму при перемещении в 30_processing/.
#
# v2.4 (14.05.2026): точка входа pipeline перенесена с корня data.up/ на data.up/20_go/.
# Корень data.up/ и data.up/10_up/ (staging) pipeline не сканирует.
#
# v2.3 (17.04.2026): добавлен stability-window (см. services/file_watcher/stability.py) —
# обычные группы попадают в pipeline только после того как size+mtime всех их файлов
# не изменились FILE_WATCHER_STABILITY_CHECKS сканов подряд. Защищает от подхвата
# недозалитых файлов (typical: GoogleDrive-синхронизация крупных ZIP).
# .delete-триггеры и reindex.* обрабатываются без задержки.
#
# Классификация групп (три типа):
#   complete   — JSON + (PDF или ZIP): полная замена отчёта, бэкап всех файлов группы, кэш инвалидируется
#   json_only  — только JSON: обновление метаданных, бэкап только .json, PDF/ZIP и кэш не затрагиваются
#   partial    — PDF/ZIP без JSON (JSON есть в data/): бэкап по расширению, кэш инвалидируется
#
# Основные возможности:
# - Сканирование и группировка файлов по ID (полные, json_only, частичные группы)
# - DELETE операции: перенос файлов из data/ + пересборка БД через единый канал
# - Усиленная валидация JSON по схеме с проверкой кодировки
# - Финальная проверка совместимости батча перед публикацией в БД
# - Атомарное копирование успешных групп с автоматическим бэкапом конфликтов
# - Публикация обновленной базы данных и автоочистка архива
# - Поддержка паузы обработки через создание директории data.up/pause
#
# Архитектура (v2.x):
# process_upload_cycle декомпозирована на 6 внутренних функций для улучшения читаемости:
# - _scan_and_separate_groups: сканирование и разделение на DELETE/обычные группы
# - _process_delete_operations: обработка операций удаления (+ пересборка БД при наличии изменений)
# - _validate_complete_groups: валидация групп с JSON (complete + json_only)
# - _process_partial_groups: обработка частичных групп без JSON
# - _perform_final_database_check: финальная проверка совместимости БД
# - _finalize_and_publish_batch: копирование, публикация БД, очистка

import logging
import shutil
from pathlib import Path
from typing import Dict

from config import (
    FILE_WATCHER_BATCH_SIZE,
    FILE_WATCHER_STABILITY_CHECKS,
    UPLOAD_PAUSE_DIRECTORY,
    DATA_DIRECTORY,
    UPLOAD_GO_DIRECTORY,
    UPLOAD_PROCESSING_DIRECTORY,
    UPLOAD_DONE_DIRECTORY,
    REINDEX_TRIGGER_PREFIX,
)
from logging_config import app_logger, log_with_data

from .scanner import (
    scan_new_files,
    group_files_by_id,
    filter_ambiguous_groups,
    filter_complete_groups,
    parse_filename,
)
from .validation import validate_json_in_processing, validate_zip_file, validate_archive_consistency
from .file_operations import (
    move_group_to_processing,
    has_json_file,
    check_data_conflicts,
    backup_to_old,
    copy_processing_to_data,
    process_partial_group,
    canonicalize_json_dopshifr,
)
from .database_generator import generate_final_database_check
from .publisher import (
    move_group_to_done,
    move_group_to_error,
    publish_database,
    cleanup_done_directory,
)
from .utils import write_error_file, get_normalized_group_id
from .deleter import process_delete_operation
from . import stability
from services.cache.cache_service import invalidate_archive_cache


# ============================================================================
# ВНУТРЕННИЕ ФУНКЦИИ ДЕКОМПОЗИЦИИ (v2.x)
# ============================================================================


def _handle_reindex_trigger(stats: dict) -> bool:
    """
    Этап 0.5: Проверка наличия файла-триггера принудительной пересборки БД.

    Ищет в data.up/20_go/ любой файл с именем reindex.* (stem == "reindex", без учёта регистра).
    При обнаружении запускает полную пересборку tlib.db из всех JSON в data/:
    - Перемещает триггер в 30_processing/
    - Вызывает generate_final_database_check([]) — пересборка без новых файлов
    - При успехе публикует БД и перемещает триггер в done/
    - При ошибке перемещает триггер в 40_error/

    Args:
        stats: словарь статистики для обновления счетчиков

    Returns:
        True если триггер был обнаружен (цикл следует завершить досрочно),
        False если триггер не найден.
    """
    upload_dir = Path(UPLOAD_GO_DIRECTORY)
    if not upload_dir.exists():
        return False

    trigger_file = None
    for f in upload_dir.iterdir():
        if f.is_file() and f.stem.lower() == REINDEX_TRIGGER_PREFIX:
            trigger_file = f
            break

    if trigger_file is None:
        return False

    app_logger.info(f"[FILE_WATCHER] Обнаружен reindex-триггер: {trigger_file.name}")

    processing_dir = Path(UPLOAD_PROCESSING_DIRECTORY)
    trigger_in_processing = processing_dir / trigger_file.name

    try:
        shutil.move(str(trigger_file), str(trigger_in_processing))
    except Exception as e:
        app_logger.error(f"[FILE_WATCHER] REINDEX: не удалось переместить триггер в 30_processing/: {e}")
        stats["errors"] += 1
        return True

    final_result = generate_final_database_check([])

    if final_result["success"]:
        published = publish_database()
        if published:
            app_logger.info("[FILE_WATCHER] REINDEX: БД успешно пересобрана и опубликована")
        else:
            app_logger.warning("[FILE_WATCHER] REINDEX: пересборка завершена, но публикация не выполнена")

        done_dir = Path(UPLOAD_DONE_DIRECTORY)
        try:
            shutil.move(str(trigger_in_processing), str(done_dir / trigger_file.name))
        except Exception as e:
            app_logger.warning(f"[FILE_WATCHER] REINDEX: не удалось переместить триггер в done/: {e}")

        stats["db_updated"] = published
    else:
        error_lines = [f"  - {e['file']}: {e['error']}" for e in final_result["errors"]]
        error_msg = "REINDEX: пересборка БД завершилась с ошибками:\n" + "\n".join(error_lines)
        app_logger.error(f"[FILE_WATCHER] {error_msg}")
        move_group_to_error("reindex", error_msg)
        stats["errors"] += 1

    return True


def _scan_and_separate_groups(stats: dict) -> tuple:
    """
    Этап 1: Сканирование файлов и разделение на группы.

    Выполняет:
    - Сканирование data.up/ на наличие новых файлов
    - Группировку файлов по ID
    - Разделение на DELETE операции и обычные группы

    Args:
        stats: словарь статистики для обновления счетчиков

    Returns:
        tuple: (regular_groups, delete_groups)
            - regular_groups: dict с обычными группами файлов
            - delete_groups: dict с DELETE операциями
    """
    # Сканирование
    files = scan_new_files()
    stats["scanned"] = len(files)

    if not files:
        return {}, {}

    app_logger.debug(f"[FILE_WATCHER] Найдено файлов: {len(files)}")

    # Группировка по нормализованному ID (Шифр → 5 цифр, ДопШифр → UPPERCASE)
    groups = group_files_by_id(files)

    # Обновляем счётчики стабильности для всех видимых файлов и чистим устаревшие записи
    all_group_files = {f for group_files in groups.values() for f in group_files}
    stability.observe(all_group_files)
    stability.prune(all_group_files)

    # Детект дубликатов: группы, где несколько исходных файлов дают одно нормализованное имя
    groups, ambiguous_groups = filter_ambiguous_groups(groups)

    for group_id, collisions in ambiguous_groups.items():
        for target_name, src_paths in collisions.items():
            src_names = ", ".join(p.name for p in src_paths)
            app_logger.warning(
                f"[FILE_WATCHER] Группа {group_id} пропущена: после нормализации "
                f"обнаружены дубликаты для целевого имени {target_name}: {src_names}. "
                f"Удалите лишний файл из data.up/20_go/, чтобы разблокировать обработку."
            )

    # Отделяем .delete операции от обычных групп;
    # обычные группы пропускаем через stability-фильтр
    delete_groups = {}
    regular_groups = {}
    unstable_ids: list[str] = []

    for group_id, group_files in groups.items():
        # .delete-триггеры — лёгкие файлы, синхронизируются мгновенно, задержка не нужна
        delete_files = [f for f in group_files if f.suffix.lower() == ".delete"]
        if delete_files:
            delete_groups[group_id] = delete_files
            continue

        # Обычная группа: ждём, пока все файлы не замрут N сканов подряд
        if not stability.is_group_stable(group_files, FILE_WATCHER_STABILITY_CHECKS):
            unstable_ids.append(group_id)
            continue

        regular_groups[group_id] = group_files

    if unstable_ids:
        app_logger.debug(
            f"[FILE_WATCHER] Нестабильные группы "
            f"(ждём {FILE_WATCHER_STABILITY_CHECKS} одинаковых сканов): {unstable_ids}"
        )

    return regular_groups, delete_groups


def _process_delete_operations(delete_groups: dict, stats: dict) -> None:
    """
    Этап 2: Обработка DELETE операций.

    Для каждой DELETE операции:
    - Парсит имя файла для получения Шифр и ДопШифр
    - Перемещает .delete файл в processing/
    - Переносит файлы группы из data/ в data.old/ (через process_delete_operation)
    - Перемещает в done/ при успехе или error/ при ошибке

    После обработки всего батча DELETE — если хотя бы один файл был перенесён из data/,
    запускает пересборку tlib-new.db и публикацию через единый канал:
    generate_final_database_check([]) → publish_database() → database_watcher_task →
    perform_database_update (swap + refresh app.state).

    Args:
        delete_groups: dict с DELETE операциями {group_id: [delete_files]}
        stats: словарь статистики для обновления счетчиков
    """
    delete_files_moved = 0

    for group_id, delete_files in delete_groups.items():
        app_logger.info(f"[FILE_WATCHER] DELETE операция для {group_id}")

        # Парсим имя файла для получения Шифр и ДопШифр
        parsed = parse_filename(delete_files[0].name)

        # Перемещаем .delete файл в processing/ для отслеживания
        if not move_group_to_processing(group_id, delete_files):
            stats["errors"] += 1
            continue

        stats["moved_to_processing"] += 1

        # Переносим файлы группы из data/ в data.old/
        success, files_moved, error_msg = process_delete_operation(
            group_id=group_id,
            shifr=parsed["shifr"],
            dopshifr=parsed["dopshifr"],
        )

        if success:
            delete_files_moved += files_moved
            if move_group_to_done(group_id):
                stats["success"] += 1
                app_logger.info(
                    f"[FILE_WATCHER] DELETE {group_id}: успешно ({files_moved} файлов перенесено)"
                )
            else:
                stats["errors"] += 1
                app_logger.error(
                    f"[FILE_WATCHER] DELETE {group_id}: данные удалены из data/, "
                    f"но триггер остался в 30_processing/ — нарушен инвариант канонизации"
                )
        else:
            write_error_file(group_id, "Ошибка удаления", error_msg)
            move_group_to_error(group_id, error_msg)
            stats["errors"] += 1
            app_logger.error(f"[FILE_WATCHER] DELETE {group_id}: {error_msg}")

    # Пересборка БД через единый канал — только если что-то реально изменилось в data/
    if delete_files_moved > 0:
        app_logger.info("[FILE_WATCHER] DELETE: пересборка tlib-new.db из data/")
        final_result = generate_final_database_check([])
        if final_result["success"]:
            if publish_database():
                stats["db_updated"] = True
                app_logger.info("[FILE_WATCHER] DELETE: tlib-new.db опубликована")
            else:
                app_logger.warning("[FILE_WATCHER] DELETE: публикация tlib-new.db не удалась")
        else:
            error_lines = [f"  - {e['file']}: {e['error']}" for e in final_result["errors"]]
            app_logger.error(
                "[FILE_WATCHER] DELETE: пересборка БД провалена:\n" + "\n".join(error_lines)
            )


def _validate_complete_groups(complete_to_process: dict, stats: dict) -> list:
    """
    Этап 3: Валидация полных групп (с JSON файлом).

    Для каждой группы:
    - Перемещает в processing/
    - Проверяет наличие JSON файла
    - Выполняет усиленную валидацию (схема + кодировка)
    - При успехе добавляет в список успешных групп
    - При ошибке перемещает в error/

    Args:
        complete_to_process: dict с полными группами для обработки
        stats: словарь статистики для обновления счетчиков

    Returns:
        list: список ID успешно провалидированных групп
    """
    successful_groups = []  # Накапливаем успешные группы

    for group_id, group_files in complete_to_process.items():
        app_logger.debug(f"[FILE_WATCHER] === Проверка группы: {group_id} ===")

        # Перемещение в processing/
        if not move_group_to_processing(group_id, group_files):
            stats["errors"] += 1
            continue

        stats["moved_to_processing"] += 1

        # Проверка ZIP файлов на Zip Bomb и другие аномалии
        processing_dir = Path(UPLOAD_PROCESSING_DIRECTORY)
        zip_files = list(processing_dir.glob(f"{group_id}.zip"))
        
        for zip_file in zip_files:
            is_valid, error_msg = validate_zip_file(zip_file)
            if not is_valid:
                log_with_data(
                    logging.WARNING,
                    "FW: ZIP валидация провалена",
                    id=group_id,
                    reason="invalid_zip",
                )
                write_error_file(group_id, "Невалидный ZIP", error_msg, location="processing")
                move_group_to_error(group_id, f"Валидация ZIP:\n{error_msg}")
                stats["errors"] += 1
                continue

        # Проверка №1 (УЛУЧШЕННАЯ): Валидация по схеме + кодировка
        if not has_json_file(group_id):
            app_logger.warning(f"[FILE_WATCHER] {group_id}: JSON файл отсутствует")
            move_group_to_error(group_id, "JSON файл не найден в группе")
            stats["errors"] += 1
            continue

        is_valid, error_msg = validate_json_in_processing(group_id)
        if not is_valid:
            # Провал валидации - группа в error
            log_with_data(
                logging.WARNING,
                "FW: валидация провалена",
                id=group_id,
                reason="invalid_json",
            )
            write_error_file(group_id, "Невалидный JSON", error_msg, location="processing")
            move_group_to_error(group_id, f"Валидация JSON:\n{error_msg}")
            stats["errors"] += 1
            continue

        # Канонизация поля ДопШифр в JSON → UPPERCASE
        if not canonicalize_json_dopshifr(group_id):
            log_with_data(
                logging.WARNING,
                "FW: канонизация ДопШифр провалена",
                id=group_id,
                reason="dopshifr_canonicalize",
            )
            write_error_file(group_id, "Ошибка канонизации ДопШифр", "", location="processing")
            move_group_to_error(group_id, "Ошибка канонизации ДопШифр в JSON")
            stats["errors"] += 1
            continue

        # Успех - добавляем в список для финальной проверки
        successful_groups.append(group_id)
        app_logger.debug(
            f"[FILE_WATCHER] {group_id}: Валидация пройдена, накоплен для финальной проверки"
        )

    return successful_groups


def _process_partial_groups(partial_to_process: dict, stats: dict) -> list:
    """
    Этап 4: Обработка частичных групп (без JSON файла).

    Частичные группы содержат только ZIP или другие файлы без JSON.
    Они не требуют валидации БД, только перемещение в processing/.

    Args:
        partial_to_process: dict с частичными группами для обработки
        stats: словарь статистики для обновления счетчиков

    Returns:
        list: список ID частичных групп
    """
    partial_groups_list = []  # Накапливаем частичные группы

    for group_id, group_files in partial_to_process.items():
        app_logger.debug(f"[FILE_WATCHER] === Частичная группа: {group_id} ===")

        # Перемещение в processing/
        if not move_group_to_processing(group_id, group_files):
            stats["errors"] += 1
            continue

        stats["moved_to_processing"] += 1

        # Проверка A+B: количество архивов и соответствие ТипФайла
        processing_dir = Path(UPLOAD_PROCESSING_DIRECTORY)
        is_valid, error_msg = validate_archive_consistency(group_id)
        if not is_valid:
            log_with_data(
                logging.WARNING,
                "FW: валидация архива провалена (частичная группа)",
                id=group_id,
                reason="archive_consistency",
            )
            write_error_file(group_id, "Несоответствие архива", error_msg, location="processing")
            move_group_to_error(group_id, error_msg)
            stats["errors"] += 1
            continue

        # Проверка ZIP файлов на Zip Bomb и другие аномалии
        zip_files = list(processing_dir.glob(f"{group_id}.zip"))
        
        for zip_file in zip_files:
            is_valid, error_msg = validate_zip_file(zip_file)
            if not is_valid:
                log_with_data(
                    logging.WARNING,
                    "FW: ZIP валидация провалена (частичная группа)",
                    id=group_id,
                    reason="invalid_zip",
                )
                write_error_file(group_id, "Невалидный ZIP", error_msg, location="processing")
                move_group_to_error(group_id, f"Валидация ZIP:\n{error_msg}")
                stats["errors"] += 1
                continue

        # Частичные группы - просто накапливаем (проверка БД не нужна)
        partial_groups_list.append(group_id)
        app_logger.debug(f"[FILE_WATCHER] {group_id}: Частичная группа накоплена")

    return partial_groups_list


def _perform_final_database_check(successful_groups: list, stats: dict) -> list:
    """
    Этап 5: Финальная проверка совместимости всего батча с БД.

    КРИТИЧНЫЙ ЭТАП: Генерирует тестовую БД из data/ + все успешные группы батча.
    Если хотя бы одна группа несовместима - ВСЕ группы батча отправляются в error/.

    Это защита от SQLite-специфичных ошибок и проблем совместимости данных,
    которые могут не обнаружиться на уровне JSON-схемы.

    Args:
        successful_groups: список ID групп, прошедших валидацию
        stats: словарь статистики для обновления счетчиков

    Returns:
        list: список ID групп, прошедших финальную проверку (может быть пустым)
    """
    if not successful_groups:
        return []

    app_logger.info(
        f"[FILE_WATCHER] Финальная проверка батча: {len(successful_groups)} групп с JSON"
    )

    final_result = generate_final_database_check(successful_groups)

    if not final_result["success"]:
        # ПРОВАЛ финальной проверки - ВСЕ группы батча в error!
        error_msg = "ПРОВАЛ ФИНАЛЬНОЙ ПРОВЕРКИ СОВМЕСТИМОСТИ:\n\n"
        for error in final_result["errors"]:
            error_msg += f"  - {error['file']}: {error['error']}\n"

        # МОНИТОРИНГ: Редкая ошибка (прошел Этап 3, провалил Этап 5)
        app_logger.warning(
            f"[FILE_WATCHER] ⚠️ РЕДКАЯ ОШИБКА: {len(successful_groups)} групп прошли валидацию (Этап 3), "
            f"но провалили финальную проверку БД (Этап 5). "
            f"Это может указывать на: "
            f"1) Проблемы совместимости данных между файлами, "
            f"2) SQLite-специфичные ошибки (overflow, блокировки), "
            f"3) Недостаточную валидацию в схеме."
        )

        app_logger.error(
            f"[FILE_WATCHER] Финальная проверка провалена! "
            f"ВСЕ {len(successful_groups)} групп батча отправляются в error/"
        )

        for group_id in successful_groups:
            log_with_data(
                logging.ERROR,
                "FW: финальная проверка провалена",
                id=group_id,
                reason="db_compatibility_check_failed",
            )
            write_error_file(group_id, "Провал финальной проверки БД", error_msg)
            move_group_to_error(group_id, error_msg)
            stats["errors"] += 1

        # Очищаем список - копирование не будет
        return []

    return successful_groups


def _finalize_and_publish_batch(
    successful_groups: list, json_only_list: list, partial_groups_list: list, stats: dict
) -> None:
    """
    Этап 6: Финализация батча - копирование, публикация БД, очистка.

    Атомарно обрабатывает все успешные группы с учётом типа:

        complete   (successful_groups, не в json_only_list):
                   бэкап ВСЕХ файлов группы из data/, copy_processing_to_data
        json_only  (successful_groups, в json_only_list):
                   бэкап только по совпадению расширений (.json), copy_processing_to_data
        partial    (partial_groups_list):
                   бэкап только по совпадению расширений (.pdf/.zip), process_partial_group

    Args:
        successful_groups: список ID групп (complete + json_only), прошедших все проверки
        json_only_list: список ID групп типа json_only (подмножество successful_groups)
        partial_groups_list: список ID частичных групп
        stats: словарь статистики для обновления счетчиков
    """
    all_groups_to_copy = successful_groups + partial_groups_list

    if all_groups_to_copy:
        complete_count = len(successful_groups) - len(
            [g for g in successful_groups if g in json_only_list]
        )
        app_logger.info(
            f"[FILE_WATCHER] Копирование батча: "
            f"{complete_count} полных + "
            f"{len([g for g in successful_groups if g in json_only_list])} только JSON + "
            f"{len(partial_groups_list)} частичных"
        )

        for group_id in all_groups_to_copy:
            # Стратегия бэкапа зависит от типа группы:
            #   complete   — все файлы группы: JSON переопределяет тип отчёта,
            #                поэтому старый ZIP/PDF тоже уходит в бэкап.
            #   json_only  — только .json: архив и кэш не затрагиваются.
            #   partial    — только совпадающие расширения: JSON не трогается.
            if group_id in partial_groups_list or group_id in json_only_list:
                files_to_backup = check_data_conflicts(group_id)
            else:
                normalized_id = get_normalized_group_id(group_id)
                files_to_backup = list(Path(DATA_DIRECTORY).glob(f"{normalized_id}.*"))

            if files_to_backup:
                app_logger.info(
                    f"[FILE_WATCHER] {group_id}: бэкап {len(files_to_backup)} файлов из data/"
                )

                if not backup_to_old(files_to_backup):
                    error_msg = "Ошибка создания бэкапа конфликтующих файлов"
                    move_group_to_error(group_id, error_msg)
                    stats["errors"] += 1
                    continue

            # Проверяем наличие ZIP/PDF в processing/ до перемещения в done/
            processing_dir = Path(UPLOAD_PROCESSING_DIRECTORY)
            has_archive_update = any(
                f.suffix.lower() in {'.zip', '.pdf'}
                for f in processing_dir.glob(f"{group_id}.*")
            )

            # Копирование в data/
            if group_id in partial_groups_list:
                success, error_details = process_partial_group(
                    group_id,
                    list(processing_dir.glob(f"{group_id}.*")),
                )
                group_type = "частичная"
            elif group_id in json_only_list:
                success, error_details = copy_processing_to_data(group_id)
                group_type = "только JSON"
            else:
                success, error_details = copy_processing_to_data(group_id)
                group_type = "полная"

            if not success:
                log_with_data(
                    logging.ERROR,
                    "FW: ошибка копирования",
                    id=group_id,
                    type=group_type,
                    error=error_details[:100],
                )
                write_error_file(group_id, "Ошибка копирования файлов", error_details)
                move_group_to_error(group_id, error_details)
                stats["errors"] += 1
                continue

            # Успех - перемещаем в done/
            if move_group_to_done(group_id):
                stats["success"] += 1
            else:
                stats["errors"] += 1
                app_logger.error(
                    f"[FILE_WATCHER] {group_id}: данные опубликованы, но файлы "
                    f"остались в 30_processing/ — нарушен инвариант канонизации"
                )

            # Инвалидация кеша при обновлении архива (ZIP или PDF).
            # json_only никогда не имеет архива → has_archive_update=False → кэш не трогается.
            if has_archive_update:
                normalized_id = get_normalized_group_id(group_id)
                deleted_cache = invalidate_archive_cache(normalized_id)
                if deleted_cache > 0:
                    app_logger.info(
                        f"[FILE_WATCHER] {group_id}: кеш инвалидирован ({deleted_cache} файлов)"
                    )

            log_with_data(
                logging.INFO,
                "FW: группа обработана",
                id=group_id,
                type=group_type,
            )

    # Публикация БД (уже сгенерирована в финальной проверке!)
    if successful_groups:
        app_logger.info("[FILE_WATCHER] Публикация БД")
        stats["db_updated"] = publish_database()

    # Очистка done/
    cleanup_done_directory()


# ============================================================================
# ГЛАВНАЯ ФУНКЦИЯ ОРКЕСТРАЦИИ
# ============================================================================


def process_upload_cycle(app_state=None) -> Dict[str, any]:
    """
    Выполняет один полный цикл обработки файлов через staged pipeline (v2.x).

    Args:
        app_state: FastAPI app.state для хранения флага паузы (опционально)

    Алгоритм:
    1. Проверка паузы (data.up/pause/)
    2. Сканирование data.up/20_go/ и разделение на DELETE/обычные группы (_scan_and_separate_groups)
    3. Обработка DELETE операций (_process_delete_operations)
    4. Фильтрация на complete/json_only/partial + ограничение BATCH_SIZE
    5. Валидация групп с JSON: complete + json_only (_validate_complete_groups)
    6. Обработка частичных групп без JSON (_process_partial_groups)
    7. Финальная проверка совместимости БД (_perform_final_database_check)
    8. Атомарное копирование + публикация БД + очистка (_finalize_and_publish_batch)

    Returns:
        dict: {
            "scanned": int,              # Количество найденных файлов
            "groups": int,               # Количество групп для обработки
            "moved_to_processing": int,  # Перемещено в 30_processing/
            "success": int,              # Успешно обработано групп
            "errors": int,               # Групп с ошибками
            "db_updated": bool           # Была ли обновлена БД
        }
    """
    # ========================================================================
    # Этап 0: Проверка паузы
    # ========================================================================
    pause_dir = Path(UPLOAD_PAUSE_DIRECTORY)
    empty_stats = {
        "scanned": 0,
        "groups": 0,
        "moved_to_processing": 0,
        "success": 0,
        "errors": 0,
        "db_updated": False,
    }

    if pause_dir.exists():
        # Логируем только при первом обнаружении паузы
        if app_state and not getattr(app_state, "file_watcher_paused", False):
            app_logger.info("[FILE_WATCHER] ⏸️  ОБРАБОТКА ПРИОСТАНОВЛЕНА (pause/ существует)")
            app_state.file_watcher_paused = True

        return empty_stats
    else:
        # Логируем возобновление если была пауза
        if app_state and getattr(app_state, "file_watcher_paused", False):
            app_logger.info("[FILE_WATCHER] ▶️  ОБРАБОТКА ВОЗОБНОВЛЕНА (pause/ удалена)")
            app_state.file_watcher_paused = False

    # ========================================================================
    # Инициализация статистики
    # ========================================================================
    stats = {
        "scanned": 0,
        "groups": 0,
        "moved_to_processing": 0,
        "success": 0,
        "errors": 0,
        "db_updated": False,
    }

    try:
        # ====================================================================
        # Этап 0.5: Проверка reindex-триггера
        # ====================================================================
        if _handle_reindex_trigger(stats):
            return stats

        # ====================================================================
        # Этап 1: Сканирование и разделение групп
        # ====================================================================
        regular_groups, delete_groups = _scan_and_separate_groups(stats)

        if not regular_groups and not delete_groups:
            return stats

        # ====================================================================
        # Этап 2: Обработка DELETE операций
        # ====================================================================
        _process_delete_operations(delete_groups, stats)

        # ====================================================================
        # Этап 3: Фильтрация и ограничение батча
        # ====================================================================
        complete_groups, json_only_groups, partial_groups = filter_complete_groups(regular_groups)
        stats["groups"] = len(complete_groups) + len(json_only_groups) + len(partial_groups)

        if not complete_groups and not json_only_groups and not partial_groups:
            return stats

        # Ограничение размера батча
        complete_to_process = dict(list(complete_groups.items())[:FILE_WATCHER_BATCH_SIZE])
        json_only_to_process = dict(list(json_only_groups.items())[:FILE_WATCHER_BATCH_SIZE])
        partial_to_process = dict(list(partial_groups.items())[:FILE_WATCHER_BATCH_SIZE])

        app_logger.info(
            f"[FILE_WATCHER] Обработка батча: "
            f"{len(complete_to_process)} полных групп, "
            f"{len(json_only_to_process)} только JSON, "
            f"{len(partial_to_process)} частичных групп"
        )

        # ====================================================================
        # Этап 4: Валидация групп с JSON (complete + json_only)
        # ====================================================================
        # Оба типа требуют валидации JSON и пересборки БД.
        # json_only_ids сохраняется отдельно для правильного бэкапа на этапе 7.
        json_only_ids = list(json_only_to_process.keys())
        all_json_groups = {**complete_to_process, **json_only_to_process}
        successful_groups = _validate_complete_groups(all_json_groups, stats)

        # ====================================================================
        # Этап 5: Обработка частичных групп
        # ====================================================================
        partial_groups_list = _process_partial_groups(partial_to_process, stats)

        # ====================================================================
        # Этап 6: Финальная проверка совместимости БД
        # ====================================================================
        validated_groups = _perform_final_database_check(successful_groups, stats)

        # ====================================================================
        # Этап 7: Финализация - копирование, публикация, очистка
        # ====================================================================
        _finalize_and_publish_batch(validated_groups, json_only_ids, partial_groups_list, stats)

    except Exception as e:
        app_logger.error(
            f"[FILE_WATCHER] Критическая ошибка в цикле обработки: {e}",
            exc_info=True,
        )

    return stats
