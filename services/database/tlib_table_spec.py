# Version 1.0 - 06.01.2026 20:29:42 GMT
# Tlib Table Spec для TlibWebApp
# Описание: Единый источник правды для структуры таблицы `tlib` в SQLite.
#           Содержит список колонок (имя, тип, ключ JSON, дефолт, преобразование),
#           и функции генерации SQL (CREATE TABLE / INSERT) и сборки values tuple.
#           Также поддерживает WARNING-only проверку дрейфа между schema.json и DB-spec.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from logging_config import app_logger


Transform = Callable[[Any], Any]


@dataclass(frozen=True)
class ColumnSpec:
    """
    Спецификация одной колонки SQLite, связанной с ключом JSON.
    """

    db_column: str
    sqlite_type: str
    json_key: str
    default: Any = None
    transform: Optional[Transform] = None

    def extract_value(self, data: Dict[str, Any]) -> Any:
        """
        Извлекает значение из JSON dict с учетом default/transform.
        """
        value = data.get(self.json_key, self.default)
        if self.transform is not None:
            try:
                return self.transform(value)
            except Exception:
                # Если трансформация падает — пусть ошибка всплывёт выше как ошибка обработки файла.
                raise
        return value


def _none_to_empty_str(value: Any) -> str:
    return "" if value is None else str(value)


def _none_to_str_strip(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _to_int_or_none(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        s = str(value).strip()
        return int(s) if s else None
    except (TypeError, ValueError):
        return None


# ВАЖНО:
# - Порядок колонок = порядок INSERT и values.
# - Состав/имена/типы соответствуют текущему `CREATE TABLE` в json_converter_service.py (21 колонка).
COLUMNS: Sequence[ColumnSpec] = (
    ColumnSpec(db_column="Шифр", sqlite_type="INTEGER", json_key="Шифр", default=None),
    ColumnSpec(db_column="ДопШифр", sqlite_type="TEXT", json_key="ДопШифр", default="", transform=_none_to_str_strip),
    ColumnSpec(db_column="Маршрут", sqlite_type="TEXT", json_key="Маршрут", default="", transform=_none_to_str_strip),
    ColumnSpec(db_column="РайонОбщий", sqlite_type="TEXT", json_key="РайонОбщий", default="", transform=_none_to_str_strip),
    ColumnSpec(db_column="Район", sqlite_type="TEXT", json_key="Район", default="", transform=_none_to_str_strip),
    ColumnSpec(db_column="Автор", sqlite_type="TEXT", json_key="Автор", default="", transform=_none_to_str_strip),
    ColumnSpec(db_column="Город", sqlite_type="TEXT", json_key="Город", default="", transform=_none_to_str_strip),
    ColumnSpec(db_column="Тип", sqlite_type="TEXT", json_key="Тип", default="", transform=_none_to_str_strip),
    ColumnSpec(db_column="ТипСудна", sqlite_type="TEXT", json_key="ТипСудна", default="", transform=_none_to_str_strip),
    ColumnSpec(db_column="КатегорияС", sqlite_type="TEXT", json_key="КатегорияС", default="", transform=_none_to_str_strip),
    ColumnSpec(db_column="КатегорияПо", sqlite_type="TEXT", json_key="КатегорияПо", default="", transform=_none_to_str_strip),
    ColumnSpec(db_column="Год", sqlite_type="INTEGER", json_key="Год", default=None),
    ColumnSpec(db_column="МесяцС", sqlite_type="INTEGER", json_key="МесяцС", default=None),
    ColumnSpec(db_column="МесяцПо", sqlite_type="INTEGER", json_key="МесяцПо", default=None),
    ColumnSpec(db_column="Комментарии", sqlite_type="TEXT", json_key="Комментарии", default="", transform=_none_to_str_strip),
    ColumnSpec(db_column="РазмерАрхива", sqlite_type="INTEGER", json_key="РазмерАрхива", default=0),
    ColumnSpec(db_column="ТипФайла", sqlite_type="TEXT", json_key="ТипФайла", default="", transform=_none_to_empty_str),
    ColumnSpec(db_column="ЗагрузилИмя", sqlite_type="TEXT", json_key="ЗагрузилИмя", default="", transform=_none_to_str_strip),
    ColumnSpec(db_column="ЗагрузилID", sqlite_type="INTEGER", json_key="ЗагрузилID", default=None, transform=_to_int_or_none),
    ColumnSpec(db_column="ДатаВремяЗагрузки", sqlite_type="TEXT", json_key="ДатаВремяЗагрузки", default="", transform=_none_to_str_strip),
    ColumnSpec(db_column="СтарыйID", sqlite_type="INTEGER", json_key="СтарыйID", default=None),
)


def _validate_table_name(table_name: str) -> str:
    """
    Минимальная защита от инъекций через table_name (внутренний параметр).
    """
    if not table_name or not isinstance(table_name, str):
        raise ValueError("table_name должен быть непустой строкой")
    ok = all(ch.isalnum() or ch == "_" for ch in table_name)
    if not ok:
        raise ValueError(f"Недопустимое имя таблицы: {table_name!r}")
    return table_name


def build_create_table_sql(table_name: str) -> str:
    """
    Генерирует CREATE TABLE IF NOT EXISTS для таблицы.
    """
    table_name = _validate_table_name(table_name)
    cols_sql = ",\n            ".join(f"{c.db_column} {c.sqlite_type}" for c in COLUMNS)
    return f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            {cols_sql}
        )
        """


def build_insert_sql(table_name: str) -> str:
    """
    Генерирует INSERT INTO ... VALUES (?, ?, ...) для таблицы.
    """
    table_name = _validate_table_name(table_name)
    columns = ", ".join(c.db_column for c in COLUMNS)
    placeholders = ", ".join(["?"] * len(COLUMNS))
    return f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})"


def build_values(data: Dict[str, Any]) -> Tuple[Any, ...]:
    """
    Собирает tuple значений для INSERT в порядке COLUMNS.
    """
    return tuple(col.extract_value(data) for col in COLUMNS)


def build_dict_from_row(row) -> Dict[str, Any]:
    """
    Преобразует строку SQLite (sqlite3.Row или dict-like) в JSON-словарь.
    Симметрично build_values: db_column -> json_key для каждой колонки в COLUMNS.
    """
    return {col.json_key: row[col.db_column] for col in COLUMNS}


def log_schema_db_drift(schema: Dict[str, Any], *, schema_name: str = "schema") -> None:
    """
    WARNING-only: логирует дрейф между schema.properties и DB-spec (COLUMNS.json_key).
    """
    try:
        schema_props = schema.get("properties") or {}
        if not isinstance(schema_props, dict):
            return

        schema_keys = set(schema_props.keys())
        db_keys = set(c.json_key for c in COLUMNS)

        in_schema_not_db = sorted(schema_keys - db_keys)
        in_db_not_schema = sorted(db_keys - schema_keys)

        if in_schema_not_db:
            app_logger.warning(
                f"[TlibTableSpec] Drift: поля есть в {schema_name}, но НЕ сохраняются в БД: "
                f"{', '.join(in_schema_not_db)}"
            )
        if in_db_not_schema:
            app_logger.warning(
                f"[TlibTableSpec] Drift: поля ожидаются DB-spec, но отсутствуют в {schema_name}: "
                f"{', '.join(in_db_not_schema)}"
            )
    except Exception:
        # Drift-check не должен ломать пайплайн.
        return
