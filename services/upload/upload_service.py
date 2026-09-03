# Version 1.8 - 26.07.2026 10:00:00 GMT
# 1.8: удалено поле ИмяФайла из JSON-отчётов (выводится из ID и ТипФайла, в БД не хранится).
# Upload Service для TlibWebApp
# Описание: Вся бизнес-логика подсистемы загрузки отчётов.
# 1.6: добавлена list_my_published_reports — список опубликованных отчётов пользователя
#      (по ЗагрузилID) из tlib.db для раздела «Мои отчёты» на странице загрузки.
# 1.7: list_my_published_reports — обработка строки обёрнута в свой try/except,
#      чтобы одна повреждённая запись в tlib.db не обнуляла весь список.
# 1.1: удалены неиспользуемые json_filename() и archive_filename() — имена строятся inline;
#      убран параметр published из send_report_decision (публикация идёт через send_report_published).
# 1.2: normalize_dopshifr и тело normalized_id -> делегаты services.id_utils;
#      pdf_page_count -> count_pdf_pages из pdf_to_png_service; taken_codes_in_db -> make_norm_id.
# 1.3: добавлены read-модели _resolve_uploader, read_staged_item, read_published_item (перенесены из upload_router).
# 1.4: do_submit/do_publish/do_submit_edit принимают file_path: Path + file_size: int вместо bytes.
#      Файл записывается через shutil.move (атомарный rename из temp). PDF-страницы считаются
#      напрямую из temp-файла без второго tmp. Temp очищается во всех early-return ветках.
# 1.5: исправлена очистка скопированного архива в do_submit_edit (ветка copy2): условие
#      archive_path_new больше не зависит от has_new_file, что предотвращало утечку сироты.
#           - нормализация и формирование ID (Шифр, ДопШифр)
#           - проверка уникальности кодов в БД, staging и processing
#           - работа с файлами staging (10_up): поиск пар, список, pdf-страницы
#           - чтение опубликованных отчётов из tlib.db и data/
#           - операции: submit, publish, reject, submit_edit, request_delete,
#             confirm_delete, reject_delete

import asyncio
import json
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import (
    BACKUP_DIRECTORY,
    DATA_DIRECTORY,
    DATABASE_PATH,
    DATABASE_TABLE_NAME,
    MAX_ARCHIVE_SIZE,
    MIME_TYPES,
    PENDING_NOTIFY_DIRECTORY,
    ROOT_ADMIN_EMAIL,
    SITE_URL,
    SQLITE_CONNECT_TIMEOUT,
    UPLOAD_GO_DIRECTORY,
    UPLOAD_PROCESSING_DIRECTORY,
    UPLOAD_STAGING_DIRECTORY,
    UPLOAD_TLIB_START_NUMBER,
)
from logging_config import app_logger
from services.auth.auth_db import get_admin_users, get_user_by_id, update_user_name
from services.auth.session_helpers import is_admin
from services.database.tlib_table_spec import build_dict_from_row
from services.id_utils import make_norm_id
from services.json_io import read_json
from services.auth.email_service import (
    send_delete_decision,
    send_delete_request_notice,
    send_new_report_notice,
    send_report_decision,
)

# Паттерн ДопШифр: до 5 букв/цифр (кириллица или латиница)
DOP_SHIFR_RE = re.compile(r'^[а-яА-Яa-zA-Z0-9]{1,5}$')


# ---------------------------------------------------------------------------
# Нормализация и имена файлов
# ---------------------------------------------------------------------------

def normalized_id(shifr: int, dop: str) -> str:
    """Канонический нормализованный ID: 00001-TLIB или 00001. Делегат make_norm_id."""
    return make_norm_id(shifr, dop)


def loose_code(s: str) -> str:
    """
    Нормализация для нестрогого сравнения: убирает пробелы, upper;
    у числовой части снимает ведущие нули.
    Примеры: '12-tlib', ' 00012 - TLIB ', '12-TLIB' → '12-TLIB'.
    """
    s = s.replace(" ", "").upper()
    parts = s.split("-", 1)
    try:
        parts[0] = str(int(parts[0]))
    except (ValueError, IndexError):
        pass
    return "-".join(parts)


# ---------------------------------------------------------------------------
# Проверка уникальности кодов
# ---------------------------------------------------------------------------

def taken_codes_in_dirs(dirs: list[Path]) -> set[str]:
    """Собирает нормализованные ID (без расширения) из .json/.zip/.pdf в указанных директориях."""
    taken: set[str] = set()
    for d in dirs:
        if not d.exists():
            continue
        for f in d.iterdir():
            if f.is_file() and f.suffix.lower() in ('.json', '.zip', '.pdf'):
                taken.add(f.stem.upper())
    return taken


def taken_codes_in_db() -> set[str]:
    """Собирает нормализованные ID из tlib.db."""
    taken: set[str] = set()
    db_path = Path(DATABASE_PATH)
    if not db_path.exists():
        return taken
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=SQLITE_CONNECT_TIMEOUT)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT Шифр, ДопШифр FROM {DATABASE_TABLE_NAME}"
        ).fetchall()
        conn.close()
        for r in rows:
            taken.add(make_norm_id(r["Шифр"], r["ДопШифр"] or ""))
    except Exception as e:
        app_logger.error(f"[upload] Ошибка чтения tlib.db для проверки уникальности: {e}")
    return taken


def all_taken_codes() -> set[str]:
    """Объединяет занятые коды из tlib.db, 10_up, 20_go, 30_processing."""
    dirs = [
        Path(UPLOAD_STAGING_DIRECTORY),
        Path(UPLOAD_GO_DIRECTORY),
        Path(UPLOAD_PROCESSING_DIRECTORY),
    ]
    return taken_codes_in_db() | taken_codes_in_dirs(dirs)


def is_taken(shifr: int, dop: str, exclude_id: str | None = None) -> bool:
    """Проверяет, занят ли Шифр-ДопШифр. exclude_id — нормализованный ID для исключения."""
    norm = normalized_id(shifr, dop)
    taken = all_taken_codes()
    if exclude_id:
        taken.discard(exclude_id.upper())
    return norm.upper() in taken


def next_free_tlib_number() -> int:
    """Первый свободный N для ДопШифр=TLIB среди всех источников."""
    taken = all_taken_codes()
    tlib_numbers: set[int] = set()
    for code in taken:
        if code.endswith("-TLIB"):
            prefix = code[:-5]
            if prefix.isdigit():
                tlib_numbers.add(int(prefix))
    n = UPLOAD_TLIB_START_NUMBER
    while n in tlib_numbers:
        n += 1
    return n


# ---------------------------------------------------------------------------
# Staging 10_up
# ---------------------------------------------------------------------------

def staging_dir() -> Path:
    p = Path(UPLOAD_STAGING_DIRECTORY)
    p.mkdir(parents=True, exist_ok=True)
    return p


def find_staged_pair(norm_id: str) -> tuple[Path | None, Path | None]:
    """Ищет json и файл архива по нормализованному ID в 10_up."""
    d = Path(UPLOAD_STAGING_DIRECTORY)
    json_path = None
    archive_path = None
    if not d.exists():
        return None, None
    for f in d.iterdir():
        if not f.is_file():
            continue
        stem_up = f.stem.upper()
        if stem_up == norm_id.upper():
            if f.suffix.lower() == '.json':
                json_path = f
            elif f.suffix.lower() in ('.zip', '.pdf'):
                archive_path = f
    return json_path, archive_path


def list_staged_reports() -> list[dict]:
    """
    Читает все JSON из 10_up и формирует список для таблицы.
    Проставляет флаг is_edit=True и orig_id из .editmeta,
    добавляет delete_request записи из .delreq файлов.
    """
    d = Path(UPLOAD_STAGING_DIRECTORY)
    if not d.exists():
        return []

    editmeta_orig: dict[str, str | None] = {}
    delreq_ids: set[str] = set()
    for f in d.iterdir():
        if f.is_file():
            stem = f.stem.upper()
            if f.suffix.lower() == '.editmeta':
                try:
                    em = read_json(f)
                    raw_orig = (em.get("orig_id") or "").strip().upper()
                    editmeta_orig[stem] = raw_orig if raw_orig and raw_orig != stem else None
                except Exception:
                    editmeta_orig[stem] = None
            elif f.suffix.lower() == '.delreq':
                delreq_ids.add(stem)

    reports = []

    for f in sorted(d.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file() and f.suffix.lower() == '.json':
            try:
                data = read_json(f)
                norm_id = f.stem.upper()
                _, archive = find_staged_pair(norm_id)
                has_archive_entry = archive is not None or (data.get("РазмерАрхива") or 0) > 0
                reports.append({
                    "id": norm_id,
                    "type": "report",
                    "shifr": data.get("Шифр"),
                    "dopshifr": data.get("ДопШифр", ""),
                    "marshrut": data.get("Маршрут", ""),
                    "autor": data.get("Автор", ""),
                    "god": data.get("Год"),
                    "zagruzil": data.get("ЗагрузилИмя", ""),
                    "data_zagruzki": data.get("ДатаВремяЗагрузки", ""),
                    "tip_faila": data.get("ТипФайла", ""),
                    "razmer": data.get("РазмерАрхива", 0),
                    "has_archive": has_archive_entry,
                    "zagruzil_id": data.get("ЗагрузилID"),
                    "is_edit": norm_id in editmeta_orig,
                    "orig_id": editmeta_orig.get(norm_id),
                })
            except Exception as e:
                app_logger.error(f"[upload] Ошибка чтения {f}: {e}")

    for f in sorted(d.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file() and f.suffix.lower() == '.delreq':
            try:
                delreq_data = read_json(f)
                norm_id = f.stem.upper()
                pub_json, pub_archive = find_published_pair(norm_id)
                pdata: dict = {}
                if pub_json:
                    try:
                        pdata = read_json(pub_json)
                    except Exception:
                        pdata = {}
                reports.append({
                    "id": norm_id,
                    "type": "delete_request",
                    "shifr": pdata.get("Шифр"),
                    "dopshifr": pdata.get("ДопШифр", ""),
                    "marshrut": pdata.get("Маршрут", ""),
                    "autor": pdata.get("Автор", ""),
                    "god": pdata.get("Год"),
                    "zagruzil": pdata.get("ЗагрузилИмя", ""),
                    "data_zagruzki": pdata.get("ДатаВремяЗагрузки", ""),
                    "tip_faila": pdata.get("ТипФайла", ""),
                    "razmer": pdata.get("РазмерАрхива", 0),
                    "has_archive": pub_archive is not None,
                    "zagruzil_id": pdata.get("ЗагрузилID"),
                    "is_edit": False,
                    "requested_by_email": delreq_data.get("requested_by_email", ""),
                    "requested_at": delreq_data.get("requested_at", ""),
                    "reason": delreq_data.get("reason", ""),
                })
            except Exception as e:
                app_logger.error(f"[upload] Ошибка чтения {f}: {e}")

    return reports


# ---------------------------------------------------------------------------
# Published: поиск в data/ и tlib.db
# ---------------------------------------------------------------------------

def parse_norm_id(norm_id: str) -> tuple[int, str]:
    """Разбирает нормализованный ID ('00001-TLIB') на (Шифр: int, ДопШифр: str)."""
    parts = norm_id.split("-", 1)
    shifr_int = int(parts[0].lstrip("0") or "0")
    dop = parts[1] if len(parts) > 1 else ""
    return shifr_int, dop


def read_published_row(norm_id: str) -> dict | None:
    """
    Читает строку опубликованного отчёта из tlib.db по нормализованному ID.
    Возвращает dict с JSON-ключами (через build_dict_from_row) или None.
    """
    db_path = Path(DATABASE_PATH)
    if not db_path.exists():
        return None
    try:
        shifr_int, dop = parse_norm_id(norm_id)
        conn = sqlite3.connect(DATABASE_PATH, timeout=SQLITE_CONNECT_TIMEOUT)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM {DATABASE_TABLE_NAME} WHERE Шифр=?",
            (shifr_int,)
        ).fetchall()
        conn.close()
        for row in rows:
            row_dop = (row["ДопШифр"] or "").strip().upper()
            if row_dop == dop:
                return build_dict_from_row(row)
        return None
    except Exception:
        return None


def list_my_published_reports(user_id: int) -> list[dict]:
    """
    Список опубликованных отчётов пользователя (ЗагрузилID = user_id) из tlib.db,
    для раздела «Мои отчёты» на странице загрузки. При ошибке — пустой список
    (не должен ронять страницу).
    """
    reports: list[dict] = []
    db_path = Path(DATABASE_PATH)
    if not db_path.exists():
        return reports
    try:
        conn = sqlite3.connect(DATABASE_PATH, timeout=SQLITE_CONNECT_TIMEOUT)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM {DATABASE_TABLE_NAME} WHERE ЗагрузилID=?",
            (user_id,)
        ).fetchall()
        conn.close()
        for row in rows:
            try:
                data = build_dict_from_row(row)
                norm_id = normalized_id(data.get("Шифр"), data.get("ДопШифр") or "")
                reports.append({
                    "id": norm_id,
                    "marshrut": data.get("Маршрут", ""),
                    "autor": data.get("Автор", ""),
                    "god": data.get("Год"),
                    "data_zagruzki": data.get("ДатаВремяЗагрузки", ""),
                    "tip_faila": data.get("ТипФайла", ""),
                    "razmer": data.get("РазмерАрхива", 0),
                    "has_archive": (data.get("РазмерАрхива") or 0) > 0,
                })
            except Exception as e:
                app_logger.error(f"[upload] Ошибка обработки строки tlib.db для my-reports user_id={user_id}: {e}")
                continue
    except Exception as e:
        app_logger.error(f"[upload] Ошибка чтения tlib.db для my-reports user_id={user_id}: {e}")
        return []

    reports.sort(key=lambda r: (r.get("data_zagruzki") or "", r.get("id") or ""), reverse=True)
    return reports


def get_published_zagruzil_id(norm_id: str) -> int | None:
    """Возвращает ЗагрузилID опубликованного отчёта из tlib.db или None."""
    row = read_published_row(norm_id)
    if row is None:
        return None
    val = row.get("ЗагрузилID")
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def is_in_library(norm_id: str) -> bool:
    """Проверяет, есть ли отчёт с таким ID в data/ или tlib.db."""
    json_path = Path(DATA_DIRECTORY) / f"{norm_id}.json"
    if json_path.exists():
        return True
    db_path = Path(DATABASE_PATH)
    if db_path.exists():
        try:
            parts = norm_id.split("-", 1)
            shifr_int = int(parts[0].lstrip("0") or "0")
            dop = parts[1] if len(parts) > 1 else ""
            conn = sqlite3.connect(DATABASE_PATH, timeout=SQLITE_CONNECT_TIMEOUT)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT ДопШифр FROM {DATABASE_TABLE_NAME} WHERE Шифр=?",
                (shifr_int,)
            ).fetchall()
            conn.close()
            return any((r["ДопШифр"] or "").strip().upper() == dop for r in rows)
        except Exception:
            pass
    return False


def find_published_pair(norm_id: str) -> tuple[Path | None, Path | None]:
    """Ищет json и файл архива по нормализованному ID в data/."""
    d = Path(DATA_DIRECTORY)
    json_path = None
    archive_path = None
    if not d.exists():
        return None, None
    for f in d.iterdir():
        if not f.is_file():
            continue
        stem_up = f.stem.upper()
        if stem_up == norm_id.upper():
            if f.suffix.lower() == '.json':
                json_path = f
            elif f.suffix.lower() in ('.zip', '.pdf'):
                archive_path = f
    return json_path, archive_path


def build_file_status(db_has: bool, db_ext: str | None, disk_has: bool, disk_ext: str | None) -> dict:
    """Сравнивает состояние файла в tlib.db и на диске (data/). Возвращает dict mismatch+message."""
    if not db_has and not disk_has:
        return {"mismatch": "none", "message": "Файл отчёта отсутствует и в базе, и на диске."}
    if db_has and not disk_has:
        return {
            "mismatch": "db_only",
            "message": f"В базе отчёт числится с файлом ({db_ext}), но на диске файла нет.",
        }
    if not db_has and disk_has:
        return {
            "mismatch": "disk_only",
            "message": f"В базе файл не указан, но на диске есть файл ({disk_ext}).",
        }
    if db_ext and disk_ext and db_ext.lower() != disk_ext.lower():
        return {
            "mismatch": "type_mismatch",
            "message": f"В базе указан тип {db_ext}, но на диске файл {disk_ext}.",
        }
    return {"mismatch": None, "message": None}


# ---------------------------------------------------------------------------
# Read-модели для роутера: staged_item и published_item
# ---------------------------------------------------------------------------

def _resolve_uploader(zagruzil_id) -> tuple[int | None, str | None, str | None]:
    """Резолвит загрузившего по ЗагрузилID -> (id, name, email). При ошибке — (None, None, None)."""
    if zagruzil_id is None:
        return None, None, None
    try:
        u = get_user_by_id(int(zagruzil_id))
        if u:
            return u["id"], u.get("name"), u.get("email")
    except Exception:
        pass
    return None, None, None


def read_staged_item(norm_id: str) -> dict | None:
    """
    Читает данные одного отчёта из 10_up по нормализованному ID.
    Возвращает None если пара не найдена (роутер -> 404).
    Пробрасывает исключение при ошибке парсинга JSON (роутер -> 500).
    """
    json_path, archive_path = find_staged_pair(norm_id)
    if not json_path:
        return None

    data = read_json(json_path)  # пробрасывает при ошибке

    uploader_id_out, uploader_name, uploader_email = _resolve_uploader(data.get("ЗагрузилID"))
    has_archive = archive_path is not None or (data.get("РазмерАрхива") or 0) > 0
    archive_ext = (
        archive_path.suffix.lstrip('.') if archive_path else data.get("ТипФайла") or None
    )

    orig_id_out: str | None = None
    editmeta_path = staging_dir() / f"{norm_id}.editmeta"
    if editmeta_path.exists():
        try:
            em = read_json(editmeta_path)
            raw_orig = (em.get("orig_id") or "").strip().upper()
            orig_id_out = raw_orig if raw_orig and raw_orig != norm_id else None
        except Exception:
            pass

    return {
        "ok": True,
        "data": data,
        "has_archive": has_archive,
        "archive_ext": archive_ext,
        "uploader_id": uploader_id_out,
        "uploader_name": uploader_name,
        "uploader_email": uploader_email,
        "orig_id": orig_id_out,
    }


def read_published_item(norm_id: str) -> dict | None:
    """
    Читает данные опубликованного отчёта из tlib.db и data/.
    Возвращает None если отчёт не найден в библиотеке (роутер -> 404).
    Не выполняет auth-проверок — это ответственность роутера.
    Возвращает полный dict, включая служебный ключ zagruzil_id (роутер снимает его через pop()
    перед формированием ответа) и delete_requested_by_email (видимость по is_admin — в роутере).
    """
    data = read_published_row(norm_id)
    if data is None:
        return None

    zagruzil_id = data.get("ЗагрузилID")
    db_has = (data.get("РазмерАрхива") or 0) > 0
    db_ext: str | None = data.get("ТипФайла") or None

    _, disk_archive = find_published_pair(norm_id)
    disk_has = disk_archive is not None
    disk_ext: str | None = disk_archive.suffix.lstrip(".") if disk_archive else None

    file_status = build_file_status(db_has, db_ext, disk_has, disk_ext)

    if disk_has:
        has_archive = True
        archive_ext = disk_ext
        data["РазмерАрхива"] = disk_archive.stat().st_size
        data["ТипФайла"] = disk_ext
    else:
        has_archive = db_has
        archive_ext = db_ext

    uploader_id_out, uploader_name, uploader_email = _resolve_uploader(zagruzil_id)

    pending_delete = False
    delete_reason: str | None = None
    delete_requested_by_email: str | None = None
    delete_requested_at: str | None = None
    delreq_path = staging_dir() / f"{norm_id}.delreq"
    if delreq_path.exists():
        try:
            delreq_data = read_json(delreq_path)
            pending_delete = True
            delete_reason = delreq_data.get("reason")
            delete_requested_by_email = delreq_data.get("requested_by_email")
            delete_requested_at = delreq_data.get("requested_at")
        except Exception:
            pass

    return {
        "ok": True,
        "data": data,
        "zagruzil_id": zagruzil_id,
        "has_archive": has_archive,
        "archive_ext": archive_ext,
        "file_status": file_status,
        "uploader_id": uploader_id_out,
        "uploader_name": uploader_name,
        "uploader_email": uploader_email,
        "pending_delete": pending_delete,
        "delete_reason": delete_reason,
        "delete_requested_by_email": delete_requested_by_email,
        "delete_requested_at": delete_requested_at,
    }


# ---------------------------------------------------------------------------
# Операция: submit (новый отчёт)
# ---------------------------------------------------------------------------

async def do_submit(
    *,
    user: dict,
    shifr: int,
    dop: str,
    marshrut: str,
    raion_obshiy: str,
    raion: str,
    avtor: str,
    tip: str,
    kategoriya_s: str,
    kategoriya_po: str,
    god: int,
    mesyats_s: int,
    mesyats_po: int,
    tip_sudna: str,
    gorod: str,
    kommentarii: str,
    zagruzil_imya: str,
    uploader_id: Optional[int],
    uploader_cleared: bool,
    file_path: Path,
    file_size: int,
    file_ext: str,
) -> dict:
    """
    Сохраняет новый отчёт в 10_up.
    file_path — уже записанный temp-файл из stream_upload_to_temp (сервис берёт ownership).
    Возвращает {"ok": True, "id": norm_id} или {"error": ..., ...} с ключом _status для HTTP-кода.
    """
    if is_taken(shifr, dop):
        file_path.unlink(missing_ok=True)
        return {"error": "Шифр-ДопШифр уже занят", "code_taken": True, "_status": 409}

    if file_size > MAX_ARCHIVE_SIZE:
        file_path.unlink(missing_ok=True)
        size_gb = MAX_ARCHIVE_SIZE / (1024 ** 3)
        return {"error": f"Файл превышает максимально допустимый размер ({size_gb:.0f} ГБ)", "_status": 413}

    norm_id = normalized_id(shifr, dop)
    staging = staging_dir()
    json_path = staging / f"{norm_id}.json"
    archive_path = staging / f"{norm_id}.{file_ext}"

    pages: int | None = None
    if file_ext == 'pdf':
        try:
            from services.conversion.pdf_to_png_service import count_pdf_pages
            pages = count_pdf_pages(file_path) or None
        except Exception as e:
            app_logger.error(f"[upload] Ошибка подсчёта страниц PDF {norm_id}: {e}")

    now_iso = datetime.now(timezone.utc).isoformat()
    report_data: dict = {
        "Шифр": shifr,
        "ДопШифр": dop if dop else None,
        "Маршрут": marshrut.strip(),
        "РайонОбщий": raion_obshiy.strip() or None,
        "Район": raion.strip() or None,
        "Автор": avtor.strip() or None,
        "Город": gorod.strip() or None,
        "Тип": tip.strip() or None,
        "ТипСудна": tip_sudna.strip() or None,
        "КатегорияС": kategoriya_s.strip() or None,
        "КатегорияПо": kategoriya_po.strip() or None,
        "Год": god,
        "МесяцС": mesyats_s if mesyats_s else None,
        "МесяцПо": mesyats_po if mesyats_po else None,
        "Комментарии": kommentarii.strip() or None,
        "РазмерАрхива": file_size,
        "ТипФайла": file_ext,
        "КоличествоСтраниц": pages,
        "ЗагрузилИмя": zagruzil_imya,
        "ЗагрузилID": user["id"],
        "ДатаВремяЗагрузки": now_iso,
    }

    if is_admin(user):
        if uploader_cleared:
            report_data["ЗагрузилID"] = None
        elif uploader_id is not None and get_user_by_id(uploader_id):
            report_data["ЗагрузилID"] = uploader_id

    try:
        json_path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding='utf-8')
        shutil.move(str(file_path), str(archive_path))
    except Exception as e:
        for p in (json_path, archive_path):
            if p.exists():
                p.unlink()
        file_path.unlink(missing_ok=True)
        app_logger.error(f"[upload] Ошибка записи в 10_up для {norm_id}: {e}", exc_info=True)
        return {"error": "Ошибка сохранения файлов", "_status": 500}

    app_logger.info(f"[upload] Новый отчёт {norm_id} сохранён в 10_up, загрузил user_id={user['id']}")

    if zagruzil_imya.strip():
        try:
            update_user_name(user["id"], zagruzil_imya.strip())
        except Exception as e:
            app_logger.error(f"[upload] Не удалось обновить name для user_id={user['id']}: {e}")

    try:
        admins = get_admin_users()
        admin_emails = [a["email"] for a in admins]
        if ROOT_ADMIN_EMAIL and ROOT_ADMIN_EMAIL not in {e.lower() for e in admin_emails}:
            admin_emails.append(ROOT_ADMIN_EMAIL)
        if admin_emails:
            await asyncio.to_thread(
                send_new_report_notice, admin_emails, norm_id, zagruzil_imya or user["email"], SITE_URL
            )
    except Exception as e:
        app_logger.error(f"[upload] Ошибка отправки уведомления админам для {norm_id}: {e}")

    return {"ok": True, "id": norm_id}


# ---------------------------------------------------------------------------
# Операция: publish
# ---------------------------------------------------------------------------

async def do_publish(
    *,
    admin: dict,
    orig_id: str,
    admin_comment: str,
    shifr: int,
    dop: str,
    marshrut: str,
    raion_obshiy: str,
    raion: str,
    avtor: str,
    tip: str,
    kategoriya_s: str,
    kategoriya_po: str,
    god: int,
    mesyats_s: int,
    mesyats_po: int,
    tip_sudna: str,
    gorod: str,
    kommentarii: str,
    zagruzil_imya: str,
    uploader_id: Optional[int],
    uploader_cleared: bool,
    remove_file: bool,
    no_email: bool,
    new_file_path: Optional[Path],
    new_file_size: int,
    new_file_ext: Optional[str],
) -> dict:
    """
    Публикует отчёт из 10_up в 20_go.
    new_file_path — уже записанный temp-файл из stream_upload_to_temp или None.
    Возвращает {"ok": True, "id": new_norm_id} или {"error": ..., "_status": ...}.
    """
    json_path, existing_archive = find_staged_pair(orig_id)
    if not json_path:
        if new_file_path:
            new_file_path.unlink(missing_ok=True)
        return {"error": "Отчёт не найден в очереди", "_status": 404}

    try:
        existing = read_json(json_path)
    except Exception as e:
        if new_file_path:
            new_file_path.unlink(missing_ok=True)
        return {"error": f"Ошибка чтения оригинального JSON: {e}", "_status": 500}

    _, data_archive = find_published_pair(orig_id)

    has_new_file = new_file_path is not None

    if has_new_file:
        if new_file_size > MAX_ARCHIVE_SIZE:
            new_file_path.unlink(missing_ok=True)
            size_gb = MAX_ARCHIVE_SIZE / (1024 ** 3)
            return {"error": f"Файл превышает максимально допустимый размер ({size_gb:.0f} ГБ)", "_status": 413}
    elif remove_file or (existing_archive is None and data_archive is None):
        return {"error": "Нельзя опубликовать без файла отчёта", "no_file": True, "_status": 400}

    new_norm_id = normalized_id(int(shifr), dop) if shifr else orig_id

    if is_taken(int(shifr), dop, exclude_id=orig_id):
        if new_file_path:
            new_file_path.unlink(missing_ok=True)
        return {"error": "Шифр-ДопШифр уже занят", "code_taken": True, "_status": 409}

    updated = dict(existing)
    updated["Шифр"] = int(shifr)
    updated["ДопШифр"] = dop if dop else None
    updated["Маршрут"] = marshrut.strip() or existing.get("Маршрут")
    updated["РайонОбщий"] = raion_obshiy.strip() or None
    updated["Район"] = raion.strip() or None
    updated["Автор"] = avtor.strip() or None
    updated["Город"] = gorod.strip() or None
    updated["Тип"] = tip.strip() or None
    updated["ТипСудна"] = tip_sudna.strip() or None
    updated["КатегорияС"] = kategoriya_s.strip() or None
    updated["КатегорияПо"] = kategoriya_po.strip() or None
    updated["Год"] = int(god)
    updated["МесяцС"] = mesyats_s if mesyats_s else None
    updated["МесяцПо"] = mesyats_po if mesyats_po else None
    updated["Комментарии"] = kommentarii.strip() or None
    updated["ЗагрузилИмя"] = zagruzil_imya

    if uploader_cleared:
        updated["ЗагрузилID"] = None
    elif uploader_id is not None and get_user_by_id(uploader_id):
        updated["ЗагрузилID"] = uploader_id

    if has_new_file:
        ext = new_file_ext
        updated["РазмерАрхива"] = new_file_size
        updated["ТипФайла"] = ext
        if ext == 'pdf':
            try:
                from services.conversion.pdf_to_png_service import count_pdf_pages
                updated["КоличествоСтраниц"] = count_pdf_pages(new_file_path) or None
            except Exception as e:
                app_logger.error(f"[upload] Ошибка подсчёта страниц PDF {new_norm_id}: {e}")
                updated["КоличествоСтраниц"] = None
        else:
            updated["КоличествоСтраниц"] = None
    else:
        archive_src = existing_archive or data_archive
        ext = archive_src.suffix.lower().lstrip('.')
    updated.pop("ИмяФайла", None)

    go_dir = Path(UPLOAD_GO_DIRECTORY)
    go_dir.mkdir(parents=True, exist_ok=True)
    target_json = go_dir / f"{new_norm_id}.json"
    target_archive = go_dir / f"{new_norm_id}.{ext}"

    json_only_path = (
        not has_new_file
        and existing_archive is None
        and data_archive is not None
        and data_archive.stem.upper() == new_norm_id.upper()
    )

    try:
        target_json.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding='utf-8')
        if has_new_file:
            shutil.move(str(new_file_path), str(target_archive))
            if existing_archive and existing_archive.exists():
                existing_archive.unlink(missing_ok=True)
        elif json_only_path:
            app_logger.info(
                f"[upload] Публикация {orig_id} -> {new_norm_id}: json_only (архив в data/ не трогается)"
            )
        elif existing_archive is not None:
            shutil.move(str(existing_archive), str(target_archive))
        else:
            shutil.copy2(str(data_archive), str(target_archive))
            app_logger.info(
                f"[upload] Публикация {orig_id} -> {new_norm_id}: архив переименован из data/"
            )
        json_path.unlink(missing_ok=True)
    except Exception as e:
        app_logger.error(f"[upload] Ошибка переноса {orig_id} в 20_go: {e}", exc_info=True)
        for p in (target_json, target_archive):
            if p and p.exists():
                p.unlink()
        if new_file_path:
            new_file_path.unlink(missing_ok=True)
        return {"error": "Ошибка публикации", "_status": 500}

    # Обработка .editmeta: при смене ID создаём .delete для исходного опубликованного отчёта
    editmeta_path = staging_dir() / f"{orig_id}.editmeta"
    if editmeta_path.exists():
        try:
            editmeta_data = read_json(editmeta_path)
            legacy_id_raw = (editmeta_data.get("orig_id") or "").strip().upper()
            if legacy_id_raw:
                legacy_norm = normalized_id(*parse_norm_id(legacy_id_raw))
                if loose_code(legacy_id_raw) != loose_code(new_norm_id):
                    delete_trigger = go_dir / f"{legacy_norm}.delete"
                    delete_trigger.touch()
                    app_logger.info(
                        f"[upload] Правка со сменой кода {legacy_norm} -> {new_norm_id}: "
                        f"создан .delete-триггер для старого ID"
                    )
        except Exception as e:
            app_logger.error(f"[upload] Ошибка чтения .editmeta для {orig_id}: {e}", exc_info=True)
        try:
            editmeta_path.unlink(missing_ok=True)
        except Exception as e:
            app_logger.error(f"[upload] Ошибка удаления .editmeta для {orig_id}: {e}")

    app_logger.info(f"[upload] Отчёт {orig_id} -> {new_norm_id} опубликован администратором {admin['email']}")

    if no_email:
        app_logger.info(f"[upload] Уведомление загрузившему подавлено (no_email) для {new_norm_id}")
    else:
        try:
            notify_dir = Path(PENDING_NOTIFY_DIRECTORY)
            notify_dir.mkdir(parents=True, exist_ok=True)
            (notify_dir / f"{new_norm_id}.notify").write_text(admin_comment or "", encoding="utf-8")
        except Exception as e:
            app_logger.error(f"[upload] Ошибка создания маркера уведомления для {new_norm_id}: {e}")

    return {"ok": True, "id": new_norm_id}


# ---------------------------------------------------------------------------
# Операция: reject
# ---------------------------------------------------------------------------

async def do_reject(
    *,
    admin: dict,
    orig_id: str,
    admin_comment: str,
    no_email: bool,
) -> dict:
    """
    Переносит отчёт из 10_up в data.old и создаёт .err файл.
    Возвращает {"ok": True} или {"error": ..., "_status": ...}.
    """
    json_path, archive_path = find_staged_pair(orig_id)
    if not json_path:
        return {"error": "Отчёт не найден в очереди", "_status": 404}

    try:
        existing = read_json(json_path)
    except Exception as e:
        return {"error": f"Ошибка чтения JSON: {e}", "_status": 500}

    backup_dir = Path(BACKUP_DIRECTORY)
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    try:
        shutil.move(str(json_path), str(backup_dir / f"{orig_id}_{ts}.json"))
        if archive_path:
            ext = archive_path.suffix.lstrip('.')
            shutil.move(str(archive_path), str(backup_dir / f"{orig_id}_{ts}.{ext}"))

        editmeta_path = staging_dir() / f"{orig_id}.editmeta"
        if editmeta_path.exists():
            shutil.move(str(editmeta_path), str(backup_dir / f"{orig_id}_{ts}.editmeta"))

        err_path = backup_dir / f"{orig_id}_{ts}.err"
        err_content = (
            f"============================================================\n"
            f"ОТЧЁТ ОТКЛОНЁН\n"
            f"============================================================\n"
            f"ID: {orig_id}\n"
            f"Время: {datetime.now(timezone.utc).isoformat()}\n"
            f"Администратор: {admin['email']}\n"
            f"\n"
            f"------------------------------------------------------------\n"
            f"КОММЕНТАРИЙ\n"
            f"------------------------------------------------------------\n"
            f"{admin_comment or '(без комментария)'}\n"
        )
        err_path.write_text(err_content, encoding='utf-8')
    except Exception as e:
        app_logger.error(f"[upload] Ошибка переноса {orig_id} в data.old: {e}", exc_info=True)
        return {"error": "Ошибка отклонения", "_status": 500}

    app_logger.info(f"[upload] Отчёт {orig_id} отклонён администратором {admin['email']}")

    if no_email:
        app_logger.info(f"[upload] Уведомление загрузившему подавлено (no_email) для {orig_id}")
    else:
        zagruzil_id = existing.get("ЗагрузилID")
        if zagruzil_id:
            try:
                uploader = get_user_by_id(int(zagruzil_id))
                if uploader:
                    await asyncio.to_thread(
                        send_report_decision, uploader["email"], orig_id, admin_comment, SITE_URL
                    )
            except Exception as e:
                app_logger.error(f"[upload] Ошибка отправки письма при отклонении {orig_id}: {e}")

    return {"ok": True}


# ---------------------------------------------------------------------------
# Операция: submit_edit (правка опубликованного)
# ---------------------------------------------------------------------------

async def do_submit_edit(
    *,
    user: dict,
    orig_id: str,
    shifr: int,
    dop: str,
    marshrut: str,
    raion_obshiy: str,
    raion: str,
    avtor: str,
    tip: str,
    kategoriya_s: str,
    kategoriya_po: str,
    god: int,
    mesyats_s: int,
    mesyats_po: int,
    tip_sudna: str,
    gorod: str,
    kommentarii: str,
    zagruzil_imya: str,
    uploader_id: Optional[int],
    uploader_cleared: bool,
    remove_file: bool,
    file_path: Optional[Path],
    file_size: int,
    file_ext: Optional[str],
) -> dict:
    """
    Сохраняет правку опубликованного отчёта в 10_up + .editmeta.
    file_path — уже записанный temp-файл из stream_upload_to_temp или None.
    Возвращает {"ok": True, "id": norm_id} или {"error": ..., "_status": ...}.
    """
    zagruzil_id_orig = get_published_zagruzil_id(orig_id)

    norm_id = normalized_id(shifr, dop)
    staging = staging_dir()

    has_new_file = file_path is not None
    archive_size: int = 0
    is_orphan: bool = False
    disk_archive_path: Path | None = None

    if has_new_file:
        if file_size > MAX_ARCHIVE_SIZE:
            file_path.unlink(missing_ok=True)
            size_gb = MAX_ARCHIVE_SIZE / (1024 ** 3)
            return {"error": f"Файл превышает максимально допустимый размер ({size_gb:.0f} ГБ)", "_status": 413}
        archive_size = file_size
    elif not remove_file:
        _, existing_archive = find_published_pair(orig_id)
        if existing_archive is not None:
            file_ext = existing_archive.suffix.lower().lstrip('.')
            archive_size = existing_archive.stat().st_size
            same_id = existing_archive.stem.upper() == norm_id.upper()
            disk_archive_path = None if same_id else existing_archive
        else:
            is_orphan = True
            file_ext = None
            archive_size = 0
    else:
        return {"error": "Нельзя сохранить правку без файла отчёта", "no_file": True, "_status": 400}

    pages: int | None = None
    if has_new_file and file_ext == 'pdf' and file_path:
        try:
            from services.conversion.pdf_to_png_service import count_pdf_pages
            pages = count_pdf_pages(file_path) or None
        except Exception as e:
            app_logger.error(f"[upload] Ошибка подсчёта страниц PDF {norm_id}: {e}")

    now_iso = datetime.now(timezone.utc).isoformat()
    report_data: dict = {
        "Шифр": shifr,
        "ДопШифр": dop if dop else None,
        "Маршрут": marshrut.strip(),
        "РайонОбщий": raion_obshiy.strip() or None,
        "Район": raion.strip() or None,
        "Автор": avtor.strip() or None,
        "Город": gorod.strip() or None,
        "Тип": tip.strip() or None,
        "ТипСудна": tip_sudna.strip() or None,
        "КатегорияС": kategoriya_s.strip() or None,
        "КатегорияПо": kategoriya_po.strip() or None,
        "Год": god,
        "МесяцС": mesyats_s if mesyats_s else None,
        "МесяцПо": mesyats_po if mesyats_po else None,
        "Комментарии": kommentarii.strip() or None,
        "РазмерАрхива": archive_size,
        "ТипФайла": file_ext,
        "КоличествоСтраниц": pages,
        "ЗагрузилИмя": zagruzil_imya,
        "ЗагрузилID": zagruzil_id_orig,
        "ДатаВремяЗагрузки": now_iso,
    }

    if is_admin(user):
        if uploader_cleared:
            report_data["ЗагрузилID"] = None
        elif uploader_id is not None and get_user_by_id(uploader_id):
            report_data["ЗагрузилID"] = uploader_id

    json_path = staging / f"{norm_id}.json"
    archive_path_new: Path | None = (staging / f"{norm_id}.{file_ext}") if file_ext else None

    try:
        json_path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding='utf-8')
        if has_new_file and file_path:
            shutil.move(str(file_path), str(archive_path_new))
        elif not is_orphan and disk_archive_path and archive_path_new:
            shutil.copy2(str(disk_archive_path), str(archive_path_new))

        editmeta = {
            "orig_id": normalized_id(*parse_norm_id(orig_id)),
            "edited_by_id": user["id"],
            "edited_by_email": user["email"],
            "edited_at": now_iso,
        }
        (staging / f"{norm_id}.editmeta").write_text(
            json.dumps(editmeta, ensure_ascii=False, indent=2), encoding='utf-8'
        )
    except Exception as e:
        cleanup_paths = [json_path, staging / f"{norm_id}.editmeta"]
        if archive_path_new:
            cleanup_paths.append(archive_path_new)
        for p in cleanup_paths:
            if p.exists():
                p.unlink()
        if file_path:
            file_path.unlink(missing_ok=True)
        app_logger.error(f"[upload] Ошибка записи правки {norm_id} в 10_up: {e}", exc_info=True)
        return {"error": "Ошибка сохранения файлов", "_status": 500}

    if is_orphan:
        app_logger.warning(
            f"[upload] Правка {orig_id} -> {norm_id} сохранена как JSON-сирота "
            f"(нет файла на диске), user_id={user['id']}"
        )
    else:
        app_logger.info(f"[upload] Правка отчёта {orig_id} -> {norm_id} сохранена в 10_up, user_id={user['id']}")

    try:
        admins = get_admin_users()
        admin_emails = [a["email"] for a in admins]
        if ROOT_ADMIN_EMAIL and ROOT_ADMIN_EMAIL not in {e.lower() for e in admin_emails}:
            admin_emails.append(ROOT_ADMIN_EMAIL)
        if admin_emails:
            await asyncio.to_thread(
                send_new_report_notice, admin_emails, norm_id,
                zagruzil_imya or user["email"], SITE_URL, is_edit=True
            )
    except Exception as e:
        app_logger.error(f"[upload] Ошибка отправки уведомления о правке {norm_id}: {e}")

    if is_orphan:
        return {
            "ok": True,
            "id": norm_id,
            "orphan": True,
            "warning": (
                "Изменения сохранены, но файл отчёта на диске не найден. "
                "JSON-сирота отправлен на рассмотрение, однако автоматически опубликован не будет — "
                "для публикации необходимо прикрепить файл."
            ),
        }
    return {"ok": True, "id": norm_id}


# ---------------------------------------------------------------------------
# Операция: request_delete
# ---------------------------------------------------------------------------

async def do_request_delete(
    *,
    user: dict,
    report_id: str,
    confirm_code: str,
    reason: str,
) -> dict:
    """
    Создаёт .delreq маркер в 10_up и отправляет письмо администраторам.
    Возвращает {"ok": True} или {"error": ..., "_status": ...}.
    """
    if loose_code(confirm_code) != loose_code(report_id):
        return {
            "error": "Введённый Шифр-ДопШифр не совпадает с отчётом",
            "code_mismatch": True,
            "_status": 409,
        }

    if not is_in_library(report_id):
        return {"error": "Отчёт не найден в библиотеке", "_status": 404}

    staging = staging_dir()
    delreq_path = staging / f"{report_id}.delreq"

    now_iso = datetime.now(timezone.utc).isoformat()
    delreq_data = {
        "report_id": report_id,
        "requested_by_id": user["id"],
        "requested_by_email": user["email"],
        "requested_at": now_iso,
        "reason": reason,
    }

    try:
        delreq_path.write_text(json.dumps(delreq_data, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception as e:
        app_logger.error(f"[upload] Ошибка создания .delreq для {report_id}: {e}", exc_info=True)
        return {"error": "Ошибка сохранения запроса", "_status": 500}

    app_logger.info(f"[upload] Запрос на удаление {report_id} создан, user_id={user['id']}")

    try:
        admins = get_admin_users()
        admin_emails = [a["email"] for a in admins]
        if ROOT_ADMIN_EMAIL and ROOT_ADMIN_EMAIL not in {e.lower() for e in admin_emails}:
            admin_emails.append(ROOT_ADMIN_EMAIL)
        if admin_emails:
            await asyncio.to_thread(
                send_delete_request_notice,
                admin_emails, report_id, user.get("name") or user["email"], SITE_URL, reason
            )
    except Exception as e:
        app_logger.error(f"[upload] Ошибка отправки уведомления о запросе удаления {report_id}: {e}")

    return {"ok": True}


# ---------------------------------------------------------------------------
# Операция: confirm_delete
# ---------------------------------------------------------------------------

async def do_confirm_delete(
    *,
    admin: dict,
    report_id: str,
    admin_comment: str,
    no_email: bool,
) -> dict:
    """
    Создаёт .delete-триггер в 20_go, удаляет .delreq маркер, отправляет письмо.
    Возвращает {"ok": True} или {"error": ..., "_status": ...}.
    """
    staging = staging_dir()
    delreq_path = staging / f"{report_id}.delreq"

    requester_email: str | None = None
    if delreq_path.exists():
        try:
            delreq_data = read_json(delreq_path)
            requester_email = delreq_data.get("requested_by_email")
        except Exception:
            pass

    go_dir = Path(UPLOAD_GO_DIRECTORY)
    go_dir.mkdir(parents=True, exist_ok=True)
    delete_trigger = go_dir / f"{report_id}.delete"

    try:
        delete_trigger.touch()
    except Exception as e:
        app_logger.error(f"[upload] Ошибка создания .delete триггера для {report_id}: {e}", exc_info=True)
        return {"error": "Ошибка создания триггера удаления", "_status": 500}

    try:
        if delreq_path.exists():
            delreq_path.unlink()
    except Exception as e:
        app_logger.error(f"[upload] Не удалось удалить .delreq для {report_id}: {e}")

    app_logger.info(f"[upload] Удаление {report_id} подтверждено администратором {admin['email']}")

    if no_email:
        app_logger.info(f"[upload] Уведомление инициатору подавлено (no_email) для {report_id}")
    elif requester_email:
        try:
            await asyncio.to_thread(
                send_delete_decision, requester_email, report_id, True, admin_comment, SITE_URL
            )
        except Exception as e:
            app_logger.error(f"[upload] Ошибка отправки письма об удалении {report_id}: {e}")

    return {"ok": True}


# ---------------------------------------------------------------------------
# Операция: reject_delete
# ---------------------------------------------------------------------------

async def do_reject_delete(
    *,
    admin: dict,
    report_id: str,
    admin_comment: str,
    no_email: bool,
) -> dict:
    """
    Удаляет .delreq маркер, отправляет письмо инициатору.
    Возвращает {"ok": True} или {"error": ..., "_status": ...}.
    """
    staging = staging_dir()
    delreq_path = staging / f"{report_id}.delreq"

    if not delreq_path.exists():
        return {"error": "Запрос на удаление не найден", "_status": 404}

    requester_email: str | None = None
    try:
        delreq_data = read_json(delreq_path)
        requester_email = delreq_data.get("requested_by_email")
    except Exception:
        pass

    try:
        delreq_path.unlink()
    except Exception as e:
        app_logger.error(f"[upload] Ошибка удаления .delreq для {report_id}: {e}", exc_info=True)
        return {"error": "Ошибка отклонения запроса", "_status": 500}

    app_logger.info(f"[upload] Запрос на удаление {report_id} отклонён администратором {admin['email']}")

    if no_email:
        app_logger.info(f"[upload] Уведомление инициатору подавлено (no_email) для {report_id}")
    elif requester_email:
        try:
            await asyncio.to_thread(
                send_delete_decision, requester_email, report_id, False, admin_comment, SITE_URL
            )
        except Exception as e:
            app_logger.error(f"[upload] Ошибка отправки письма об отклонении удаления {report_id}: {e}")

    return {"ok": True}
