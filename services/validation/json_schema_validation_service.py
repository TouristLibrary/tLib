# Version 3.1 - 15.06.2026 10:18:00 GMT
# JSON Schema Validation Service для TlibWebApp
# Описание: Модуль валидации JSON отчетов по JSON Schema (assets/schema.json).
#           read_json_file() делегирует чтение в services.json_io.read_json (BOM-устойчивое);
#           добавляет dict-проверку корневого JSON и экспортирует для совместимости с file_watcher.
#           Загружает/кэширует схему по mtime; валидирует файлы и dict-данные.
#           Логирует предупреждения о дополнительных полях и детальные ошибки jsonschema.

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Tuple

from jsonschema import validate as jsonschema_validate
from jsonschema import ValidationError

from logging_config import app_logger
from services.json_io import read_json


@dataclass(frozen=True)
class _SchemaCacheEntry:
    mtime_ns: int
    schema: Dict[str, Any]


_SCHEMA_CACHE: Dict[str, _SchemaCacheEntry] = {}


def read_json_file(json_path: Path) -> Dict[str, Any]:
    """
    Читает JSON файл в dict с поддержкой UTF-8 BOM.
    Делегирует чтение в services.json_io.read_json; добавляет dict-проверку корня.

    Args:
        json_path: Путь к JSON файлу

    Returns:
        dict: распарсенный JSON

    Raises:
        json.JSONDecodeError: при синтаксической ошибке JSON
        OSError / FileNotFoundError: при проблемах чтения файла
        ValueError: если корневой JSON не является объектом
    """
    data = read_json(json_path)
    if not isinstance(data, dict):
        raise ValueError("Корневой JSON должен быть объектом (dict)")
    return data


def load_schema(schema_path: Path) -> Dict[str, Any]:
    """
    Загружает JSON Schema и кэширует её по пути + mtime.

    Args:
        schema_path: Путь к файлу схемы

    Returns:
        dict: JSON Schema

    Raises:
        json.JSONDecodeError / OSError: при ошибках чтения/парсинга схемы
        ValueError: если схема не является объектом
    """
    key = str(schema_path.resolve())
    try:
        mtime_ns = schema_path.stat().st_mtime_ns
    except OSError:
        # Если schema_path не читается/не существует, пусть вызывающий обработает исключение.
        raise

    cached = _SCHEMA_CACHE.get(key)
    if cached and cached.mtime_ns == mtime_ns:
        return cached.schema

    with open(schema_path, 'r', encoding='utf-8-sig') as f:
        schema = json.load(f)
    if not isinstance(schema, dict):
        raise ValueError("JSON Schema должна быть объектом (dict)")

    _SCHEMA_CACHE[key] = _SchemaCacheEntry(mtime_ns=mtime_ns, schema=schema)
    return schema


def _log_extra_fields_warning(json_name: str, data: Dict[str, Any], schema: Dict[str, Any]) -> None:
    """
    Логирует предупреждение о дополнительных полях, не описанных в schema.properties.

    Важно: schema.json может иметь additionalProperties=true; это предупреждение не блокирует обработку.
    """
    try:
        allowed_fields = set((schema.get("properties") or {}).keys())
        if not allowed_fields:
            return
        extra_fields = set(data.keys()) - allowed_fields
        if extra_fields:
            app_logger.warning(
                f"[JSON_SCHEMA] {json_name}: "
                f"Обнаружены дополнительные поля (могут быть проигнорированы при загрузке в БД): "
                f"{', '.join(sorted(extra_fields))}"
            )
    except Exception:
        # Никогда не ломаем валидацию из-за логики предупреждения.
        return


def validate_json_data(data: Dict[str, Any], schema: Dict[str, Any], json_name: str = "JSON") -> Tuple[bool, str]:
    """
    Валидирует уже распарсенный JSON dict по переданной схеме.

    Args:
        data: dict-данные JSON
        schema: dict JSON Schema
        json_name: Имя для логов (обычно filename)

    Returns:
        (is_valid: bool, error_message: str)
    """
    try:
        _log_extra_fields_warning(json_name=json_name, data=data, schema=schema)
        jsonschema_validate(instance=data, schema=schema)
        return True, ""

    except ValidationError as e:
        error_path = " -> ".join(str(p) for p in e.path) if e.path else "корень"
        failed_value = str(getattr(e, "instance", "N/A"))[:100]
        validator = getattr(e, "validator", "N/A")

        error_msg = (
            f"Ошибка валидации по схеме:\n"
            f"  Поле: {error_path}\n"
            f"  Проблема: {e.message}\n"
            f"  Значение: {failed_value}\n"
            f"  Валидатор: {validator}"
        )
        app_logger.error(f"[JSON_SCHEMA] {json_name}: {error_msg}")
        return False, error_msg

    except Exception as e:
        error_msg = f"Неожиданная ошибка валидации: {e}"
        app_logger.error(f"[JSON_SCHEMA] {json_name}: {error_msg}", exc_info=True)
        return False, error_msg


def validate_json_file(json_path: Path, schema_path: Path) -> Tuple[bool, str]:
    """
    Валидирует JSON файл по схеме.

    Это удобная обёртка: читает JSON, грузит schema (с кэшем), валидирует.
    """
    try:
        data = read_json_file(json_path)
        schema = load_schema(schema_path)
        return validate_json_data(data=data, schema=schema, json_name=json_path.name)

    except json.JSONDecodeError as e:
        error_msg = f"JSON синтаксическая ошибка: {e}"
        app_logger.error(f"[JSON_SCHEMA] {json_path.name}: {error_msg}")
        return False, error_msg

    except Exception as e:
        error_msg = f"Неожиданная ошибка валидации: {e}"
        app_logger.error(f"[JSON_SCHEMA] {json_path.name}: {error_msg}", exc_info=True)
        return False, error_msg


def validate_json_against_schema(json_path: Path, schema_path: Path) -> Tuple[bool, str]:
    """
    Backward-compatible alias для старого API.
    """
    return validate_json_file(json_path=json_path, schema_path=schema_path)
