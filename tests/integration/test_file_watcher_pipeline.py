# Version 1.2 - 14.06.2026 13:40:00 GMT
# Integration tests: File Watcher pipeline сквозные сценарии
# Описание: Проверяет process_upload_cycle() на реальной файловой системе в tmp-директориях.
#           Патчит config.* (ленивые импорты внутри функций) и services.file_watcher.pipeline.*
#           (module-level импорты на старте модуля). stability._observations сбрасывается перед
#           каждым тестом для изоляции.
# 1.2: monkeypatch переведён с TEMP_DATABASE_PATH на DATABASE_NEW_FILE.

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _make_valid_zip(path: Path) -> None:
    """Создаёт минимальный валидный ZIP-файл."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("report.txt", "content")
    path.write_bytes(buf.getvalue())


def _valid_json(shifr: int, dopshifr: str = "TST", tipfayla: str = "zip") -> dict:
    """Минимальный JSON отчёта, проходящий assets/schema.json."""
    return {
        "Шифр": shifr,
        "ДопШифр": dopshifr,
        "Маршрут": f"Тестовый маршрут {shifr}",
        "РазмерАрхива": 0,
        "ТипФайла": tipfayla,
    }


# ---------------------------------------------------------------------------
# Фикстура окружения pipeline
# ---------------------------------------------------------------------------


@pytest.fixture()
def fw_env(tmp_path, monkeypatch):
    """
    Создаёт изолированное окружение pipeline в tmp_path.

    Патчит:
    - config.*  — для модулей, импортирующих константы лениво (inside function body)
    - services.file_watcher.pipeline.* — module-level привязки при старте модуля
    - FILE_WATCHER_STABILITY_CHECKS = 1 — мгновенная стабильность (одного скана достаточно)
    - stability._observations.clear() — сброс между тестами

    Возвращает dict с Path для каждой директории.
    """
    import services.file_watcher.pipeline as fw_pipeline
    import services.file_watcher.deleter as fw_deleter
    import services.file_watcher.stability as stability
    import services.cache.cache_service as cache_service

    dirs = {
        "go":         tmp_path / "20_go",
        "processing": tmp_path / "30_processing",
        "error":      tmp_path / "40_error",
        "data":       tmp_path / "data",
        "old":        tmp_path / "data.old",
        "done":       tmp_path / "data.new",
        "db":         tmp_path / "data.db",
        "pause":      tmp_path / "pause",  # не создаётся — нужно явно
    }
    for name, d in dirs.items():
        if name != "pause":
            d.mkdir(parents=True, exist_ok=True)

    db_path = str(dirs["db"] / "tlib.db")
    temp_db = str(tmp_path / "tlib-new.db")

    # Патч config.* (ленивые импорты)
    for attr, val in [
        ("UPLOAD_GO_DIRECTORY",         str(dirs["go"])),
        ("UPLOAD_PROCESSING_DIRECTORY", str(dirs["processing"])),
        ("UPLOAD_ERROR_DIRECTORY",      str(dirs["error"])),
        ("UPLOAD_DONE_DIRECTORY",       str(dirs["done"])),
        ("UPLOAD_PAUSE_DIRECTORY",      str(dirs["pause"])),
        ("DATA_DIRECTORY",              str(dirs["data"])),
        ("BACKUP_DIRECTORY",            str(dirs["old"])),
        ("DATABASE_PATH",               db_path),
        ("DATABASE_NEW_FILE",           temp_db),
        ("CACHE_DIRECTORY",             str(tmp_path / "cache")),
    ]:
        monkeypatch.setattr("config." + attr, val)

    # Патч pipeline.* (module-level привязки)
    for attr, val in [
        ("UPLOAD_GO_DIRECTORY",         str(dirs["go"])),
        ("UPLOAD_PROCESSING_DIRECTORY", str(dirs["processing"])),
        ("UPLOAD_DONE_DIRECTORY",       str(dirs["done"])),
        ("UPLOAD_PAUSE_DIRECTORY",      str(dirs["pause"])),
        ("DATA_DIRECTORY",              str(dirs["data"])),
        ("FILE_WATCHER_STABILITY_CHECKS", 1),
    ]:
        monkeypatch.setattr(fw_pipeline, attr, val)

    # Патч прочих module-level привязок в дереве вызовов
    monkeypatch.setattr(fw_deleter, "DATA_DIRECTORY", str(dirs["data"]))
    monkeypatch.setattr(cache_service, "CACHE_DIRECTORY", str(tmp_path / "cache"))

    # Сброс global stability state
    stability._observations.clear()

    dirs["db_path"]  = db_path
    dirs["temp_db"]  = temp_db
    dirs["tmp_path"] = tmp_path
    return dirs


# ---------------------------------------------------------------------------
# Сценарий 1: Валидная complete-группа
# ---------------------------------------------------------------------------


def test_valid_complete_group(fw_env):
    """
    JSON + ZIP → data/, группа в done/, БД пересобрана и опубликована.
    """
    from services.file_watcher.pipeline import process_upload_cycle

    json_file = fw_env["go"] / "00500-NEW.json"
    zip_file  = fw_env["go"] / "00500-NEW.zip"
    _write_json(json_file, _valid_json(500, "NEW"))
    _make_valid_zip(zip_file)

    stats = process_upload_cycle()

    assert stats["success"] == 1, stats
    assert stats["errors"] == 0, stats
    assert stats["db_updated"] is True, stats

    # Файлы скопированы в data/
    assert (fw_env["data"] / "00500-NEW.json").exists()
    assert (fw_env["data"] / "00500-NEW.zip").exists()

    # Группа перемещена в done/
    assert any(fw_env["done"].glob("00500-NEW.*"))

    # tlib-new.db опубликована в data.db/
    assert (Path(fw_env["db_path"]).parent / "tlib-new.db").exists()


# ---------------------------------------------------------------------------
# Сценарий 2: Невалидный JSON → 40_error/
# ---------------------------------------------------------------------------


def test_invalid_json_goes_to_error(fw_env):
    """
    JSON с нарушением схемы (Шифр=0) → группа в 40_error/, data/ пустой.
    """
    from services.file_watcher.pipeline import process_upload_cycle

    bad_json = {"Шифр": 0, "Маршрут": "Плохой маршрут"}
    json_file = fw_env["go"] / "00901-BAD.json"
    zip_file  = fw_env["go"] / "00901-BAD.zip"
    _write_json(json_file, bad_json)
    _make_valid_zip(zip_file)

    stats = process_upload_cycle()

    assert stats["errors"] >= 1, stats
    assert stats["success"] == 0, stats

    # Ничего не попало в data/
    assert not list(fw_env["data"].glob("00901-BAD.*"))

    # Файлы отправлены в 40_error/
    assert any(fw_env["error"].glob("00901-BAD.*"))


# ---------------------------------------------------------------------------
# Сценарий 3: Partial-группа (ZIP без JSON в 20_go/, JSON уже в data/)
# ---------------------------------------------------------------------------


def test_partial_group_updates_archive(fw_env):
    """
    ZIP в 20_go/ при наличии JSON в data/ → архив скопирован в data/,
    success=1, БД не пересобирается (partial-only батч).
    """
    from services.file_watcher.pipeline import process_upload_cycle

    # JSON уже в data/ (ТипФайла = zip — указывает, что архив должен быть)
    _write_json(
        fw_env["data"] / "00777-OLD.json",
        {"Шифр": 777, "ДопШифр": "OLD", "Маршрут": "Уже в базе", "ТипФайла": "zip", "РазмерАрхива": 0},
    )

    # В 20_go/ только новый ZIP (без JSON)
    zip_file = fw_env["go"] / "00777-OLD.zip"
    _make_valid_zip(zip_file)

    stats = process_upload_cycle()

    assert stats["success"] == 1, stats
    assert stats["errors"] == 0, stats
    # Partial-only — БД не публикуется
    assert stats["db_updated"] is False, stats

    # Архив скопирован в data/
    assert (fw_env["data"] / "00777-OLD.zip").exists()

    # Группа в done/
    assert any(fw_env["done"].glob("00777-OLD.*"))


# ---------------------------------------------------------------------------
# Сценарий 4: Конфликт → старые файлы в data.old/, новые в data/
# ---------------------------------------------------------------------------


def test_conflict_backup_and_replace(fw_env):
    """
    Полная группа для уже существующего ID → старые файлы переезжают в data.old/,
    новые попадают в data/.
    """
    from services.file_watcher.pipeline import process_upload_cycle

    # Старые файлы в data/
    old_json = fw_env["data"] / "00800-BAK.json"
    old_zip  = fw_env["data"] / "00800-BAK.zip"
    _write_json(old_json, _valid_json(800, "BAK"))
    _make_valid_zip(old_zip)

    # Новые файлы в 20_go/
    _write_json(fw_env["go"] / "00800-BAK.json", {**_valid_json(800, "BAK"), "Маршрут": "Новый маршрут"})
    _make_valid_zip(fw_env["go"] / "00800-BAK.zip")

    stats = process_upload_cycle()

    assert stats["success"] == 1, stats
    assert stats["errors"] == 0, stats

    # Старые файлы должны оказаться в data.old/ (с timestamp в имени)
    backup_jsons = list(fw_env["old"].glob("00800-BAK_*.json"))
    backup_zips  = list(fw_env["old"].glob("00800-BAK_*.zip"))
    assert len(backup_jsons) == 1, f"backup json: {list(fw_env['old'].iterdir())}"
    assert len(backup_zips)  == 1, f"backup zip: {list(fw_env['old'].iterdir())}"

    # Новые файлы в data/
    assert (fw_env["data"] / "00800-BAK.json").exists()
    assert (fw_env["data"] / "00800-BAK.zip").exists()


# ---------------------------------------------------------------------------
# Сценарий 5: Пауза — цикл возвращает нули
# ---------------------------------------------------------------------------


def test_pause_directory_halts_processing(fw_env):
    """
    При наличии pause/ цикл возвращает нулевую статистику, файлы остаются в 20_go/.
    """
    from services.file_watcher.pipeline import process_upload_cycle

    # Создаём pause/
    fw_env["pause"].mkdir(parents=True, exist_ok=True)

    # Кладём файлы в 20_go/
    _write_json(fw_env["go"] / "00999-PAU.json", _valid_json(999, "PAU"))
    _make_valid_zip(fw_env["go"] / "00999-PAU.zip")

    stats = process_upload_cycle()

    # Всё по нулям
    assert stats["scanned"] == 0
    assert stats["success"] == 0
    assert stats["errors"] == 0

    # Файлы остались нетронутыми
    assert (fw_env["go"] / "00999-PAU.json").exists()
    assert (fw_env["go"] / "00999-PAU.zip").exists()


# ---------------------------------------------------------------------------
# Сценарий 6: Stability-window
# ---------------------------------------------------------------------------


def test_stability_window_delays_processing(fw_env, monkeypatch):
    """
    При FILE_WATCHER_STABILITY_CHECKS=3 первые два цикла не трогают группу,
    третий — обрабатывает.
    """
    import services.file_watcher.pipeline as fw_pipeline
    from services.file_watcher.pipeline import process_upload_cycle

    # Переопределяем stability threshold = 3 для этого теста
    monkeypatch.setattr(fw_pipeline, "FILE_WATCHER_STABILITY_CHECKS", 3)

    json_file = fw_env["go"] / "00555-WIN.json"
    zip_file  = fw_env["go"] / "00555-WIN.zip"
    _write_json(json_file, _valid_json(555, "WIN"))
    _make_valid_zip(zip_file)

    # Первый цикл: count=1, required=3 → нет обработки
    s1 = process_upload_cycle()
    assert s1["success"] == 0, f"cycle 1: {s1}"
    assert json_file.exists(), "файл пропал после первого цикла"

    # Второй цикл: count=2
    s2 = process_upload_cycle()
    assert s2["success"] == 0, f"cycle 2: {s2}"
    assert json_file.exists()

    # Третий цикл: count=3 → обрабатывается
    s3 = process_upload_cycle()
    assert s3["success"] == 1, f"cycle 3: {s3}"
    assert not json_file.exists(), "файл должен быть перемещён из 20_go/"


# ---------------------------------------------------------------------------
# Сценарий 7: .delete-триггер
# ---------------------------------------------------------------------------


def test_delete_trigger_removes_files_from_data(fw_env):
    """
    .delete-триггер → файлы из data/ уходят в data.old/,
    триггер в done/, БД пересобрана.
    """
    from services.file_watcher.pipeline import process_upload_cycle

    # Файлы, которые должны быть удалены
    _write_json(fw_env["data"] / "00600-DEL.json", _valid_json(600, "DEL"))
    _make_valid_zip(fw_env["data"] / "00600-DEL.zip")

    # Survivor: чтобы финальная пересборка БД вернула success=True
    _write_json(
        fw_env["data"] / "00601-SRV.json",
        {"Шифр": 601, "ДопШифр": "SRV", "Маршрут": "Выжил", "РазмерАрхива": 0},
    )

    # .delete-триггер в 20_go/
    (fw_env["go"] / "00600-DEL.delete").write_bytes(b"")

    stats = process_upload_cycle()

    assert stats["success"] == 1, stats
    assert stats["errors"] == 0, stats
    assert stats["db_updated"] is True, stats

    # Файлы группы ушли из data/
    assert not (fw_env["data"] / "00600-DEL.json").exists()
    assert not (fw_env["data"] / "00600-DEL.zip").exists()

    # Файлы теперь в data.old/
    assert any(fw_env["old"].glob("00600-DEL*"))

    # Триггер перемещён в done/
    assert any(fw_env["done"].glob("00600-DEL.delete"))

    # Survivor остался в data/
    assert (fw_env["data"] / "00601-SRV.json").exists()
