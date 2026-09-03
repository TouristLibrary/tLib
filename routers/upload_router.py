# Version 2.5 - 05.07.2026 21:15:00 GMT
# Upload Router для TlibWebApp
# Описание: Тонкий HTTP-слой API загрузки отчётов. Вся бизнес-логика — в services/upload/upload_service.py.
# 2.5: новый GET /api/upload/my-reports — список опубликованных отчётов текущего
#      пользователя для раздела «Мои отчёты» (доступен всем авторизованным).
# 2.0: роутер сокращён до валидации входа + auth-check + вызов сервиса; бизнес-логика вынесена.
# 2.1: read-модели staged_item/published_item вынесены в upload_service (read_staged_item, read_published_item).
# 2.2: published-item убирает служебный ключ zagruzil_id из ответа через model.pop() (восстановлен исходный JSON-контракт).
# 2.3: await file.read() заменён потоковой записью через stream_upload_to_temp (защита RAM);
#      disk-guard в submit/submit-edit (507 при нехватке места); новый GET /api/upload/status.
# 2.4: _disk_full_response — немедленный URGENT-алерт DISK_LOW при блокировке аплоада
#      (троттлинг 30 мин встроен в send_admin_alert; /status алерт не шлёт).
#
#   GET  /api/upload/next-code          — первый свободный N для дефолтного шифра N-TLIB
#   GET  /api/upload/check-code         — проверка уникальности Шифр-ДопШифр (+ can_edit для опубликованных)
#   POST /api/upload/submit             — сохранить отчёт в data.up/10_up
#   GET  /api/upload/list               — список отчётов в data.up/10_up (только для админов)
#   GET  /api/upload/my-reports         — список опубликованных отчётов текущего пользователя
#   GET  /api/upload/item               — данные одного отчёта из 10_up (только для админов)
#   GET  /api/upload/file               — скачать файл отчёта из 10_up (только для админов)
#   POST /api/upload/publish            — перенести пару json+файл в data.up/20_go (только для админов)
#   POST /api/upload/reject             — перенести в data.old + .err с комментарием (только для админов)
#   GET  /api/upload/published-item     — данные опубликованного отчёта из data/ (автор или админ)
#   POST /api/upload/submit-edit        — правка опубликованного отчёта → сохранить в data.up/10_up + .editmeta
#   POST /api/upload/request-delete     — запрос удаления опубликованного отчёта (автор или админ)
#   POST /api/upload/confirm-delete     — подтвердить удаление: создать .delete в 20_go (только для админов)
#   POST /api/upload/reject-delete      — отклонить запрос на удаление (только для админов)

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse

from config import MAX_ARCHIVE_SIZE, MIME_TYPES
from logging_config import app_logger
from services.alerts.alerter import send_admin_alert
from services.auth.auth_db import get_user_by_id, get_user_by_email
from services.auth.session_helpers import (
    get_current_user,
    get_admin_user,
    can_edit_report,
    is_admin,
    unauthorized,
    forbidden,
)
from services.upload.upload_io import (
    UploadTooLargeError,
    disk_allows_upload,
    stream_upload_to_temp,
)
from services.upload.upload_service import (
    DOP_SHIFR_RE,
    normalized_id,
    next_free_tlib_number,
    is_taken,
    find_staged_pair,
    find_published_pair,
    list_staged_reports,
    list_my_published_reports,
    get_published_zagruzil_id,
    is_in_library,
    read_staged_item,
    read_published_item,
    staging_dir,
    do_submit,
    do_publish,
    do_reject,
    do_submit_edit,
    do_request_delete,
    do_confirm_delete,
    do_reject_delete,
)

router = APIRouter(prefix="/api/upload", tags=["upload"])


def _json_response(result: dict) -> JSONResponse:
    """Превращает dict-результат сервиса в JSONResponse (извлекает _status)."""
    status = result.pop("_status", 200)
    return JSONResponse(result, status_code=status)


def _disk_full_response(disk_info: dict, endpoint: str) -> JSONResponse:
    """Лог + немедленный URGENT DISK_LOW-алерт (троттлинг 30 мин) + 507.

    Вызывается в ветках disk-guard submit/submit-edit.
    /status — намеренно не использует этот хелпер, алерт там не нужен.
    """
    app_logger.warning(
        f"[upload] Загрузка {endpoint} заблокирована: диск заполнен "
        f"(used={disk_info.get('used_pct')}%, free={disk_info.get('free_gb')} ГБ, "
        f"reason={disk_info.get('reason')})"
    )
    send_admin_alert(
        "DISK_LOW",
        disk_used_pct=disk_info.get("used_pct"),
        disk_free_gb=disk_info.get("free_gb"),
        reason=disk_info.get("reason"),
        source=f"upload:{endpoint}",
    )
    return JSONResponse(
        {"error": "Загрузка временно недоступна: на сервере недостаточно места на диске. "
                  "Обратитесь к администратору."},
        status_code=507,
    )


# ---------------------------------------------------------------------------
# Endpoints: коды
# ---------------------------------------------------------------------------

@router.get("/next-code")
def next_code(request: Request):
    """Первый свободный N для Шифр-ДопШифр=N-TLIB."""
    if not get_current_user(request):
        return unauthorized()
    n = next_free_tlib_number()
    return JSONResponse({"shifr": n, "dopshifr": "TLIB"})


@router.get("/check-code")
def check_code(request: Request, shifr: int, dopshifr: str = "", exclude: str = ""):
    """Проверяет уникальность Шифр-ДопШифр. Возвращает {taken, normalized, in_library, can_edit, zagruzil_id}."""
    user = get_current_user(request)
    if not user:
        return unauthorized()
    dop = dopshifr.strip().upper()
    norm = normalized_id(shifr, dop)
    taken = is_taken(shifr, dop, exclude_id=exclude.strip().upper() or None)

    in_library = False
    can_edit_flag = False
    zagruzil_id = None

    if taken:
        in_library = is_in_library(norm)
        if in_library:
            zagruzil_id = get_published_zagruzil_id(norm)
            can_edit_flag = can_edit_report(user, zagruzil_id)

    return JSONResponse({
        "taken": taken,
        "normalized": norm,
        "in_library": in_library,
        "can_edit": can_edit_flag,
        "zagruzil_id": zagruzil_id,
    })


@router.get("/lookup-user")
def lookup_user(request: Request, id: int | None = None, email: str | None = None):
    """Резолв пользователя auth.db по id или email. Только для администраторов."""
    if not get_admin_user(request):
        return unauthorized()
    if id is not None:
        u = get_user_by_id(id)
    elif email:
        u = get_user_by_email(email.strip())
    else:
        return JSONResponse({"found": False})
    if not u:
        return JSONResponse({"found": False})
    return JSONResponse({"found": True, "id": u["id"], "email": u["email"], "name": u.get("name", "")})


# ---------------------------------------------------------------------------
# Endpoints: submit (новый отчёт)
# ---------------------------------------------------------------------------

@router.get("/status")
def upload_status(request: Request):
    """Возвращает состояние подсистемы загрузки: разрешены ли загрузки и метрики диска."""
    user = get_current_user(request)
    if not user:
        return unauthorized()

    allowed, info = disk_allows_upload(staging_dir())
    return JSONResponse({
        "uploads_enabled": allowed,
        "reason": info.get("reason"),
        "free_gb": info.get("free_gb", 0.0),
        "used_pct": info.get("used_pct", 0),
    })


@router.post("/submit")
async def submit(
    request: Request,
    shifr: int = Form(...),
    dopshifr: str = Form("TLIB"),
    marshrut: str = Form(...),
    raion_obshiy: str = Form(""),
    raion: str = Form(""),
    avtor: str = Form(""),
    tip: str = Form(""),
    kategoriya_s: str = Form(""),
    kategoriya_po: str = Form(""),
    god: int = Form(...),
    mesyats_s: int = Form(0),
    mesyats_po: int = Form(0),
    tip_sudna: str = Form(""),
    gorod: str = Form(""),
    kommentarii: str = Form(""),
    zagruzil_imya: str = Form(""),
    uploader_id: Optional[int] = Form(None),
    uploader_cleared: bool = Form(False),
    file: UploadFile = File(...),
):
    user = get_current_user(request)
    if not user:
        return unauthorized()

    ext = Path(file.filename).suffix.lower().lstrip('.')
    if ext not in ('zip', 'pdf'):
        return JSONResponse({"error": "Допустимые форматы файла: zip, pdf"}, status_code=400)

    dop = dopshifr.strip().upper()
    if dop and not DOP_SHIFR_RE.match(dop):
        return JSONResponse({"error": "ДопШифр: до 5 букв/цифр (кириллица или латиница)"}, status_code=400)

    # Disk guard: блокируем до начала стрима
    allowed, disk_info = disk_allows_upload(staging_dir())
    if not allowed:
        return _disk_full_response(disk_info, "submit")

    try:
        file_path, file_size = await stream_upload_to_temp(
            file, staging_dir(), MAX_ARCHIVE_SIZE
        )
    except UploadTooLargeError:
        size_gb = MAX_ARCHIVE_SIZE / (1024 ** 3)
        return JSONResponse(
            {"error": f"Файл превышает максимально допустимый размер ({size_gb:.0f} ГБ)"},
            status_code=413,
        )

    result = await do_submit(
        user=user,
        shifr=shifr,
        dop=dop,
        marshrut=marshrut,
        raion_obshiy=raion_obshiy,
        raion=raion,
        avtor=avtor,
        tip=tip,
        kategoriya_s=kategoriya_s,
        kategoriya_po=kategoriya_po,
        god=god,
        mesyats_s=mesyats_s,
        mesyats_po=mesyats_po,
        tip_sudna=tip_sudna,
        gorod=gorod,
        kommentarii=kommentarii,
        zagruzil_imya=zagruzil_imya,
        uploader_id=uploader_id,
        uploader_cleared=uploader_cleared,
        file_path=file_path,
        file_size=file_size,
        file_ext=ext,
    )
    return _json_response(result)


# ---------------------------------------------------------------------------
# Endpoints: staging list/item/file
# ---------------------------------------------------------------------------

@router.get("/list")
def staged_list(request: Request):
    """Список отчётов в data.up/10_up. Только для админов."""
    if not get_admin_user(request):
        return unauthorized()
    reports = list_staged_reports()
    return JSONResponse({"ok": True, "reports": reports})


@router.get("/my-reports")
def my_reports(request: Request):
    """Список опубликованных отчётов текущего пользователя (по ЗагрузилID). Доступен всем авторизованным."""
    user = get_current_user(request)
    if not user:
        return unauthorized()
    reports = list_my_published_reports(user["id"])
    return JSONResponse({"ok": True, "reports": reports})


@router.get("/item")
def staged_item(request: Request, id: str):
    """JSON одного отчёта из 10_up по нормализованному ID. Только для админов."""
    if not get_admin_user(request):
        return unauthorized()
    norm_id = id.upper()
    try:
        data = read_staged_item(norm_id)
        if data is None:
            return JSONResponse({"error": "Отчёт не найден"}, status_code=404)
        return JSONResponse(data)
    except Exception as e:
        app_logger.error(f"[upload] Ошибка чтения staged item {norm_id}: {e}")
        return JSONResponse({"error": "Ошибка чтения отчёта"}, status_code=500)


@router.get("/file")
def staged_file(request: Request, id: str):
    """Скачать файл архива отчёта из data.up/10_up (или data/ для json-only правок). Только для админов."""
    if not get_admin_user(request):
        return unauthorized()
    norm_id = id.strip().upper()
    _, archive_path = find_staged_pair(norm_id)
    if archive_path is None or not archive_path.exists():
        _, archive_path = find_published_pair(norm_id)
    if not archive_path or not archive_path.exists():
        return JSONResponse({"error": "Файл не найден"}, status_code=404)
    ext = archive_path.suffix.lower().lstrip('.')
    media_type = MIME_TYPES.get(f'.{ext}', 'application/octet-stream')
    resp = FileResponse(str(archive_path), media_type=media_type, filename=archive_path.name)
    resp.headers["Content-Disposition"] = f'attachment; filename="{archive_path.name}"'
    return resp


# ---------------------------------------------------------------------------
# Endpoints: publish / reject
# ---------------------------------------------------------------------------

@router.post("/publish")
async def publish(
    request: Request,
    id: str = Form(...),
    admin_comment: str = Form(""),
    shifr: int = Form(...),
    dopshifr: str = Form(""),
    marshrut: str = Form(""),
    raion_obshiy: str = Form(""),
    raion: str = Form(""),
    avtor: str = Form(""),
    tip: str = Form(""),
    kategoriya_s: str = Form(""),
    kategoriya_po: str = Form(""),
    god: int = Form(...),
    mesyats_s: int = Form(0),
    mesyats_po: int = Form(0),
    tip_sudna: str = Form(""),
    gorod: str = Form(""),
    kommentarii: str = Form(""),
    zagruzil_imya: str = Form(""),
    uploader_id: Optional[int] = Form(None),
    uploader_cleared: bool = Form(False),
    remove_file: bool = Form(False),
    no_email: bool = Form(False),
    file: Optional[UploadFile] = File(None),
):
    admin = get_admin_user(request)
    if not admin:
        return unauthorized()

    orig_id = id.strip().upper()
    if not orig_id:
        return JSONResponse({"error": "id обязателен"}, status_code=400)

    dop = dopshifr.strip().upper()

    new_file_path: Optional[Path] = None
    new_file_size: int = 0
    new_file_ext: Optional[str] = None
    has_new_file = file is not None and file.filename
    if has_new_file:
        new_file_ext = Path(file.filename).suffix.lower().lstrip('.')
        if new_file_ext not in ('zip', 'pdf'):
            return JSONResponse({"error": "Допустимые форматы файла: zip, pdf"}, status_code=400)
        try:
            new_file_path, new_file_size = await stream_upload_to_temp(
                file, staging_dir(), MAX_ARCHIVE_SIZE
            )
        except UploadTooLargeError:
            size_gb = MAX_ARCHIVE_SIZE / (1024 ** 3)
            return JSONResponse(
                {"error": f"Файл превышает максимально допустимый размер ({size_gb:.0f} ГБ)"},
                status_code=413,
            )

    result = await do_publish(
        admin=admin,
        orig_id=orig_id,
        admin_comment=admin_comment.strip(),
        shifr=shifr,
        dop=dop,
        marshrut=marshrut,
        raion_obshiy=raion_obshiy,
        raion=raion,
        avtor=avtor,
        tip=tip,
        kategoriya_s=kategoriya_s,
        kategoriya_po=kategoriya_po,
        god=god,
        mesyats_s=mesyats_s,
        mesyats_po=mesyats_po,
        tip_sudna=tip_sudna,
        gorod=gorod,
        kommentarii=kommentarii,
        zagruzil_imya=zagruzil_imya,
        uploader_id=uploader_id,
        uploader_cleared=uploader_cleared,
        remove_file=remove_file,
        no_email=no_email,
        new_file_path=new_file_path,
        new_file_size=new_file_size,
        new_file_ext=new_file_ext,
    )
    return _json_response(result)


@router.post("/reject")
async def reject(request: Request):
    """Перемещает отчёт из 10_up в data.old с timestamp и создаёт .err файл."""
    admin = get_admin_user(request)
    if not admin:
        return unauthorized()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    orig_id = (body.get("id") or "").strip().upper()
    if not orig_id:
        return JSONResponse({"error": "id обязателен"}, status_code=400)

    result = await do_reject(
        admin=admin,
        orig_id=orig_id,
        admin_comment=(body.get("admin_comment") or "").strip(),
        no_email=bool(body.get("no_email")),
    )
    return _json_response(result)


# ---------------------------------------------------------------------------
# Endpoints: published-item / submit-edit / request-delete / confirm-delete / reject-delete
# ---------------------------------------------------------------------------

@router.get("/published-item")
def published_item(request: Request, id: str):
    """Данные опубликованного отчёта из tlib.db. Доступен автору (ЗагрузилID) и администраторам."""
    user = get_current_user(request)
    if not user:
        return unauthorized()

    norm_id = id.strip().upper()
    model = read_published_item(norm_id)
    if model is None:
        return JSONResponse({"error": "Отчёт не найден в библиотеке"}, status_code=404)

    zagruzil_id = model.pop("zagruzil_id", None)
    if not can_edit_report(user, zagruzil_id):
        return forbidden()

    if not is_admin(user):
        model["delete_requested_by_email"] = None

    return JSONResponse(model)


@router.post("/submit-edit")
async def submit_edit(
    request: Request,
    edit_orig_id: str = Form(...),
    shifr: int = Form(...),
    dopshifr: str = Form("TLIB"),
    marshrut: str = Form(...),
    raion_obshiy: str = Form(""),
    raion: str = Form(""),
    avtor: str = Form(""),
    tip: str = Form(""),
    kategoriya_s: str = Form(""),
    kategoriya_po: str = Form(""),
    god: int = Form(...),
    mesyats_s: int = Form(0),
    mesyats_po: int = Form(0),
    tip_sudna: str = Form(""),
    gorod: str = Form(""),
    kommentarii: str = Form(""),
    zagruzil_imya: str = Form(""),
    uploader_id: Optional[int] = Form(None),
    uploader_cleared: bool = Form(False),
    remove_file: bool = Form(False),
    file: Optional[UploadFile] = File(None),
):
    user = get_current_user(request)
    if not user:
        return unauthorized()

    orig_id = edit_orig_id.strip().upper()
    if not orig_id:
        return JSONResponse({"error": "edit_orig_id обязателен"}, status_code=400)

    zagruzil_id_orig = get_published_zagruzil_id(orig_id)
    if not can_edit_report(user, zagruzil_id_orig):
        return forbidden()

    dop = dopshifr.strip().upper()
    if dop and not DOP_SHIFR_RE.match(dop):
        return JSONResponse({"error": "ДопШифр: до 5 букв/цифр (кириллица или латиница)"}, status_code=400)

    if is_taken(shifr, dop, exclude_id=orig_id):
        return JSONResponse({"error": "Шифр-ДопШифр уже занят", "code_taken": True}, status_code=409)

    # Disk guard: блокируем до начала стрима (только для пользовательского пути)
    allowed, disk_info = disk_allows_upload(staging_dir())
    if not allowed:
        return _disk_full_response(disk_info, "submit-edit")

    file_path: Optional[Path] = None
    file_size: int = 0
    file_ext: Optional[str] = None
    has_new_file = file is not None and file.filename
    if has_new_file:
        file_ext = Path(file.filename).suffix.lower().lstrip('.')
        if file_ext not in ('zip', 'pdf'):
            return JSONResponse({"error": "Допустимые форматы файла: zip, pdf"}, status_code=400)
        try:
            file_path, file_size = await stream_upload_to_temp(
                file, staging_dir(), MAX_ARCHIVE_SIZE
            )
        except UploadTooLargeError:
            size_gb = MAX_ARCHIVE_SIZE / (1024 ** 3)
            return JSONResponse(
                {"error": f"Файл превышает максимально допустимый размер ({size_gb:.0f} ГБ)"},
                status_code=413,
            )

    result = await do_submit_edit(
        user=user,
        orig_id=orig_id,
        shifr=shifr,
        dop=dop,
        marshrut=marshrut,
        raion_obshiy=raion_obshiy,
        raion=raion,
        avtor=avtor,
        tip=tip,
        kategoriya_s=kategoriya_s,
        kategoriya_po=kategoriya_po,
        god=god,
        mesyats_s=mesyats_s,
        mesyats_po=mesyats_po,
        tip_sudna=tip_sudna,
        gorod=gorod,
        kommentarii=kommentarii,
        zagruzil_imya=zagruzil_imya,
        uploader_id=uploader_id,
        uploader_cleared=uploader_cleared,
        remove_file=remove_file,
        file_path=file_path,
        file_size=file_size,
        file_ext=file_ext,
    )
    return _json_response(result)


@router.post("/request-delete")
async def request_delete(request: Request):
    """Создаёт запрос на удаление опубликованного отчёта. Доступен автору и администраторам."""
    user = get_current_user(request)
    if not user:
        return unauthorized()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    report_id    = (body.get("id")           or "").strip().upper()
    confirm_code = (body.get("confirm_code") or "").strip().upper()
    reason       = (body.get("reason")       or "").strip()

    if not report_id:
        return JSONResponse({"error": "id обязателен"}, status_code=400)
    if not confirm_code:
        return JSONResponse({"error": "confirm_code обязателен"}, status_code=400)
    if not reason:
        return JSONResponse({"error": "reason обязателен"}, status_code=400)

    zagruzil_id = get_published_zagruzil_id(report_id)
    if not can_edit_report(user, zagruzil_id):
        return forbidden()

    result = await do_request_delete(
        user=user,
        report_id=report_id,
        confirm_code=confirm_code,
        reason=reason,
    )
    return _json_response(result)


@router.post("/confirm-delete")
async def confirm_delete(request: Request):
    """Подтверждает удаление: создаёт .delete-триггер в 20_go. Только для администраторов."""
    admin = get_admin_user(request)
    if not admin:
        return unauthorized()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    report_id     = (body.get("id")            or "").strip().upper()
    admin_comment = (body.get("admin_comment") or "").strip()
    no_email = bool(body.get("no_email"))
    if not report_id:
        return JSONResponse({"error": "id обязателен"}, status_code=400)

    result = await do_confirm_delete(
        admin=admin,
        report_id=report_id,
        admin_comment=admin_comment,
        no_email=no_email,
    )
    return _json_response(result)


@router.post("/reject-delete")
async def reject_delete(request: Request):
    """Отклоняет запрос на удаление. Только для администраторов."""
    admin = get_admin_user(request)
    if not admin:
        return unauthorized()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    report_id     = (body.get("id")            or "").strip().upper()
    admin_comment = (body.get("admin_comment") or "").strip()
    no_email = bool(body.get("no_email"))
    if not report_id:
        return JSONResponse({"error": "id обязателен"}, status_code=400)

    result = await do_reject_delete(
        admin=admin,
        report_id=report_id,
        admin_comment=admin_comment,
        no_email=no_email,
    )
    return _json_response(result)
