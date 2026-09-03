# Version 3.12 - 10.07.2026 09:45:00 GMT
# Admin Router для TlibWebApp
# Описание: Информационная страница администратора.
# 3.12: GET/POST /api/admin/hidden-reports — список скрытых отчётов (см. services/hidden_reports.py);
#           POST обновляет app.state.hidden_reports сразу после сохранения настройки.
# 3.2: auth-хелперы (_get_admin_user, _unauthorized, _forbidden) вынесены в services/auth/session_helpers.
# 3.3: удалён неиспользуемый импорт forbidden (_forbidden — 0 вызовов).
# 3.4: подключение к БД переведено на open_tlib_db() (read-only); убраны прямые sqlite3/SQLITE_CONNECT_TIMEOUT.
# 3.5: _LOG_KV_RE/_parse_log_kv -> parse_logfmt_fields из logging_config (единый парсер).
# 3.6: _collect_* вынесены в services/admin/status_service (collect_health, collect_status).
# 3.7: панель управления пользователями — GET /api/admin/users, GET /api/admin/sessions,
#           POST /api/admin/users/activate, /deactivate, /delete.
# 3.8: _check_self_and_root убран из activate (активация безопасна, не нужна защита).
# 3.9: удалён эндпоинт /api/admin/users/delete — удаление только через CLI.
# 3.10: GET /api/admin/sessions теперь содержит token_hash и is_current;
#           POST /api/admin/sessions/delete — завершение конкретной сессии.
# 3.11: рефакторинг панели 7 — единые таблицы пользователей:
#           /api/admin/users теперь возвращает is_root, is_self, last_login_ip, active_session_count;
#           добавлен POST /api/admin/users/logout (завершить все сессии пользователя;
#             для своей учётки — все, кроме текущей);
#           удалены GET /api/admin/sessions и POST /api/admin/sessions/delete.
#           GET  /admin                — отдаёт admin.html (публично, содержимое зависит от роли)
#           GET  /api/admin/health-brief — общий статус здоровья (публично, для хедера)
#           GET  /api/admin/status     — полный JSON статуса (только для админов)
#           GET  /api/admin/admins     — список админов (только для админов)
#           POST /api/admin/grant      — выдать права админа по email (только для админов)
#           POST /api/admin/revoke     — отобрать права у выбранных (только для админов)
#           GET  /api/admin/settings   — получить настройки (только для админов)
#           POST /api/admin/settings   — сохранить настройки (только для админов)
#           POST /api/admin/test-email — отправить тестовое письмо (только для админов)
#           GET  /api/admin/users      — список всех пользователей (только для админов)
#           POST /api/admin/users/activate   — разблокировать пользователя (только для админов)
#           POST /api/admin/users/deactivate — заблокировать + закрыть сессии (только для админов)
#           POST /api/admin/users/logout     — завершить сессии пользователя (только для админов)
#           GET  /api/admin/hidden-reports   — список скрытых отчётов (только для админов)
#           POST /api/admin/hidden-reports   — сохранить список скрытых отчётов (только для админов)
#           Аутентификация: cookie session_token + role='admin' или ROOT_ADMIN_EMAIL.

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from config import (
    AUTH_COOKIE_NAME,
    ROOT_ADMIN_EMAIL,
    UPLOAD_GO_DIRECTORY,
    UPLOAD_PAUSE_DIRECTORY,
    UPLOAD_PROCESSING_DIRECTORY,
    DIGEST_DEFAULT_SEND_TIME,
)
from services.admin.status_service import collect_health, collect_status
from services.auth.auth_db import (
    delete_user_sessions,
    find_or_create_user,
    get_admin_users,
    get_all_users,
    get_setting,
    get_user_by_email,
    hash_token,
    set_setting,
    set_user_active,
    set_user_role,
)
from services.auth.session_helpers import get_admin_user as _get_admin_user
from services.auth.session_helpers import unauthorized as _unauthorized
from services.alerts.alerter import send_admin_alert_direct
from services.alerts.recipients import collect_admin_emails
from services.hidden_reports import (
    HIDDEN_REPORTS_SETTING,
    format_for_storage,
    parse_and_normalize,
)
from logging_config import app_logger

router = APIRouter(tags=["admin"])


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/admin")
def admin_page(request: Request):
    """
    Страница администратора — отдаёт admin.html всем.
    JS на клиенте проверяет /api/auth/me и показывает содержимое в зависимости от роли.
    """
    return FileResponse("admin.html", media_type="text/html")


@router.get("/api/admin/health-brief")
def admin_health_brief(request: Request):
    """
    Минимальный статус здоровья системы — доступен без аутентификации.
    Используется для отображения индикатора в хедере для всех пользователей.
    """
    try:
        h = collect_health(request.app.state)
        return JSONResponse({"overall": h.get("overall", "unhealthy")})
    except Exception as e:
        app_logger.error(f"[admin] Ошибка health-brief: {e}")
        return JSONResponse({"overall": "unhealthy"})


@router.get("/api/admin/status")
def admin_status(request: Request):
    """
    Полный JSON со статусом системы. Только для авторизованных админов.
    """
    if not _get_admin_user(request):
        return _unauthorized()

    try:
        return JSONResponse(collect_status(request.app.state))
    except Exception as e:
        app_logger.error(f"[admin] Ошибка сборки статуса: {e}", exc_info=True)
        return JSONResponse({"error": "Internal error collecting status"}, status_code=500)


@router.get("/api/admin/admins")
def admin_list(request: Request):
    """
    Список пользователей с правами admin. Только для авторизованных админов.
    ROOT_ADMIN_EMAIL помечается флагом is_root=true.
    """
    if not _get_admin_user(request):
        return _unauthorized()

    try:
        admins = get_admin_users()
        result = []
        for a in admins:
            is_root = bool(ROOT_ADMIN_EMAIL and a["email"] == ROOT_ADMIN_EMAIL)
            result.append({"email": a["email"], "name": a["name"], "is_root": is_root})
        # Ensure ROOT_ADMIN_EMAIL is in the list even if not in DB yet
        if ROOT_ADMIN_EMAIL:
            emails_in_list = {a["email"] for a in result}
            if ROOT_ADMIN_EMAIL not in emails_in_list:
                result.insert(0, {"email": ROOT_ADMIN_EMAIL, "name": "", "is_root": True})
        return JSONResponse({"admins": result})
    except Exception as e:
        app_logger.error(f"[admin] Ошибка получения списка админов: {e}", exc_info=True)
        return JSONResponse({"error": "Internal error"}, status_code=500)


@router.post("/api/admin/grant")
async def admin_grant(request: Request):
    """
    Выдаёт права admin пользователю по email.
    Создаёт пользователя в БД если его ещё нет. Только для авторизованных админов.
    """
    if not _get_admin_user(request):
        return _unauthorized()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return JSONResponse({"error": "Некорректный email"}, status_code=400)

    try:
        find_or_create_user(email, "")
        set_user_role(email, "admin")
        app_logger.info(f"[admin] Granted admin to {email}")
        return JSONResponse({"ok": True})
    except Exception as e:
        app_logger.error(f"[admin] Ошибка выдачи прав: {e}", exc_info=True)
        return JSONResponse({"error": "Internal error"}, status_code=500)


@router.post("/api/admin/pause")
def admin_toggle_pause(request: Request):
    """
    Переключает паузу обработки файлов.
    Создаёт data.up/pause если не существует, удаляет если существует.
    Только для авторизованных админов.
    """
    if not _get_admin_user(request):
        return _unauthorized()

    pause_dir = Path(UPLOAD_PAUSE_DIRECTORY)
    try:
        if pause_dir.exists():
            pause_dir.rmdir()
            paused = False
            app_logger.info("[admin] Пауза обработки снята")
        else:
            pause_dir.mkdir(parents=True, exist_ok=True)
            paused = True
            app_logger.info("[admin] Пауза обработки установлена")
        return JSONResponse({"paused": paused})
    except Exception as e:
        app_logger.error(f"[admin] Ошибка переключения паузы: {e}", exc_info=True)
        return JSONResponse({"error": "Internal error"}, status_code=500)


@router.post("/api/admin/reindex")
def admin_reindex(request: Request):
    """
    Запускает принудительную пересборку БД путём создания файла-триггера reindex.trigger в data.up/20_go/.
    Если триггер уже существует (в очереди или обрабатывается) — возвращает ошибку.
    Только для авторизованных админов.
    """
    if not _get_admin_user(request):
        return _unauthorized()

    go_dir = Path(UPLOAD_GO_DIRECTORY)
    processing_dir = Path(UPLOAD_PROCESSING_DIRECTORY)

    for check_dir in (go_dir, processing_dir):
        if check_dir.exists():
            for f in check_dir.iterdir():
                if f.is_file() and f.stem.lower() == "reindex":
                    return JSONResponse({"error": "Реиндексация уже выполняется"}, status_code=409)

    try:
        go_dir.mkdir(parents=True, exist_ok=True)
        trigger = go_dir / "reindex.trigger"
        trigger.touch()
        app_logger.info("[admin] Создан reindex-триггер")
        return JSONResponse({"ok": True})
    except Exception as e:
        app_logger.error(f"[admin] Ошибка создания reindex-триггера: {e}", exc_info=True)
        return JSONResponse({"error": "Internal error"}, status_code=500)


@router.get("/api/admin/reindex-status")
def admin_reindex_status(request: Request):
    """
    Возвращает текущий статус реиндексации.
    Ищет файл reindex.* в data.up/20_go/ и data.up/30_processing/.
    Только для авторизованных админов.
    """
    if not _get_admin_user(request):
        return _unauthorized()

    go_dir = Path(UPLOAD_GO_DIRECTORY)
    processing_dir = Path(UPLOAD_PROCESSING_DIRECTORY)

    if go_dir.exists():
        for f in go_dir.iterdir():
            if f.is_file() and f.stem.lower() == "reindex":
                return JSONResponse({"status": "queued"})

    if processing_dir.exists():
        for f in processing_dir.iterdir():
            if f.is_file() and f.stem.lower() == "reindex":
                return JSONResponse({"status": "processing"})

    return JSONResponse({"status": "idle"})


@router.post("/api/admin/revoke")
async def admin_revoke(request: Request):
    """
    Отбирает права admin у указанных пользователей.
    ROOT_ADMIN_EMAIL защищён — его права нельзя отобрать через этот endpoint.
    Только для авторизованных админов.
    """
    if not _get_admin_user(request):
        return _unauthorized()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    emails = body.get("emails") or []
    if not isinstance(emails, list):
        return JSONResponse({"error": "emails must be a list"}, status_code=400)

    revoked = []
    skipped = []
    try:
        for email in emails:
            email = (email or "").strip().lower()
            if not email:
                continue
            if ROOT_ADMIN_EMAIL and email == ROOT_ADMIN_EMAIL:
                skipped.append(email)
                continue
            set_user_role(email, "")
            revoked.append(email)
            app_logger.info(f"[admin] Revoked admin from {email}")
        return JSONResponse({"ok": True, "revoked": revoked, "skipped": skipped})
    except Exception as e:
        app_logger.error(f"[admin] Ошибка отбора прав: {e}", exc_info=True)
        return JSONResponse({"error": "Internal error"}, status_code=500)


@router.get("/api/admin/settings")
def admin_get_settings(request: Request):
    """Возвращает настройки приложения. Только для авторизованных админов."""
    if not _get_admin_user(request):
        return _unauthorized()
    try:
        digest_time = get_setting("digest_send_time", DIGEST_DEFAULT_SEND_TIME)
        return JSONResponse({"digest_send_time": digest_time})
    except Exception as e:
        app_logger.error(f"[admin] Ошибка получения настроек: {e}", exc_info=True)
        return JSONResponse({"error": "Internal error"}, status_code=500)


@router.post("/api/admin/settings")
async def admin_save_settings(request: Request):
    """Сохраняет настройки приложения. Только для авторизованных админов."""
    if not _get_admin_user(request):
        return _unauthorized()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    digest_time = (body.get("digest_send_time") or "").strip()
    if digest_time:
        import re as _re
        if not _re.match(r"^\d{2}:\d{2}$", digest_time):
            return JSONResponse({"error": "Некорректный формат времени (ожидается ЧЧ:ММ)"}, status_code=400)
        h, m = int(digest_time.split(":")[0]), int(digest_time.split(":")[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return JSONResponse({"error": "Некорректное время"}, status_code=400)
        try:
            set_setting("digest_send_time", digest_time)
            app_logger.info(f"[admin] Время дайджеста изменено на {digest_time} МСК")
        except Exception as e:
            app_logger.error(f"[admin] Ошибка сохранения настроек: {e}", exc_info=True)
            return JSONResponse({"error": "Internal error"}, status_code=500)

    return JSONResponse({"ok": True})


@router.get("/api/admin/hidden-reports")
def admin_get_hidden_reports(request: Request):
    """Возвращает текущий список скрытых отчётов (текст для textarea). Только для админов."""
    if not _get_admin_user(request):
        return _unauthorized()
    try:
        text = get_setting(HIDDEN_REPORTS_SETTING, "")
        ids, _invalid = parse_and_normalize(text)
        return JSONResponse({"text": format_for_storage(ids)})
    except Exception as e:
        app_logger.error(f"[admin] Ошибка получения списка скрытых отчётов: {e}", exc_info=True)
        return JSONResponse({"error": "Internal error"}, status_code=500)


@router.post("/api/admin/hidden-reports")
async def admin_save_hidden_reports(request: Request):
    """Сохраняет список скрытых отчётов. Пустой список допустим (нет скрытых). Только для админов."""
    admin = _get_admin_user(request)
    if not admin:
        return _unauthorized()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    text = body.get("text") or ""
    ids, invalid = parse_and_normalize(text)
    if invalid:
        return JSONResponse(
            {"error": "Некорректный формат: " + ", ".join(invalid)},
            status_code=400,
        )

    try:
        stored_text = format_for_storage(ids)
        set_setting(HIDDEN_REPORTS_SETTING, stored_text)
        request.app.state.hidden_reports = set(ids)
        app_logger.info(f"[admin] Список скрытых отчётов обновлён ({len(ids)} шт.) администратором {admin.get('email')}")
        return JSONResponse({"ok": True, "text": stored_text})
    except Exception as e:
        app_logger.error(f"[admin] Ошибка сохранения списка скрытых отчётов: {e}", exc_info=True)
        return JSONResponse({"error": "Internal error"}, status_code=500)


@router.get("/api/admin/users")
def admin_users_list(request: Request):
    """Список всех пользователей auth.db с флагами is_root, is_self и active_session_count.
    Только для авторизованных админов."""
    admin = _get_admin_user(request)
    if not admin:
        return _unauthorized()

    try:
        users = get_all_users()
        current_email = admin.get("email", "")
        result = []
        for u in users:
            is_root = bool(ROOT_ADMIN_EMAIL and u["email"] == ROOT_ADMIN_EMAIL)
            is_self = u["email"] == current_email
            result.append({**u, "is_root": is_root, "is_self": is_self})
        return JSONResponse({"users": result})
    except Exception as e:
        app_logger.error(f"[admin] Ошибка получения списка пользователей: {e}", exc_info=True)
        return JSONResponse({"error": "Internal error"}, status_code=500)


def _check_self_and_root(request: Request, email: str):
    """Возвращает JSONResponse с ошибкой, если email — ROOT_ADMIN_EMAIL или текущий админ."""
    if ROOT_ADMIN_EMAIL and email == ROOT_ADMIN_EMAIL:
        return JSONResponse({"error": "Нельзя изменить суперадмина"}, status_code=403)
    admin = _get_admin_user(request)
    if admin and admin.get("email") == email:
        return JSONResponse({"error": "Нельзя изменить собственную учётную запись"}, status_code=403)
    return None


@router.post("/api/admin/users/activate")
async def admin_user_activate(request: Request):
    """Разблокирует пользователя. Только для авторизованных админов."""
    if not _get_admin_user(request):
        return _unauthorized()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return JSONResponse({"error": "Некорректный email"}, status_code=400)

    try:
        if not get_user_by_email(email):
            return JSONResponse({"error": "Пользователь не найден"}, status_code=404)
        set_user_active(email, 1)
        app_logger.info(f"[admin] Пользователь {email} разблокирован")
        return JSONResponse({"ok": True})
    except Exception as e:
        app_logger.error(f"[admin] Ошибка активации пользователя: {e}", exc_info=True)
        return JSONResponse({"error": "Internal error"}, status_code=500)


@router.post("/api/admin/users/deactivate")
async def admin_user_deactivate(request: Request):
    """Блокирует пользователя и закрывает все его сессии. Только для авторизованных админов."""
    if not _get_admin_user(request):
        return _unauthorized()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return JSONResponse({"error": "Некорректный email"}, status_code=400)

    guard = _check_self_and_root(request, email)
    if guard:
        return guard

    try:
        user = get_user_by_email(email)
        if not user:
            return JSONResponse({"error": "Пользователь не найден"}, status_code=404)
        set_user_active(email, 0)
        delete_user_sessions(user["id"])
        app_logger.info(f"[admin] Пользователь {email} заблокирован, сессии удалены")
        return JSONResponse({"ok": True})
    except Exception as e:
        app_logger.error(f"[admin] Ошибка блокировки пользователя: {e}", exc_info=True)
        return JSONResponse({"error": "Internal error"}, status_code=500)


@router.post("/api/admin/users/logout")
async def admin_user_logout(request: Request):
    """Завершает все активные сессии пользователя по email.
    Для своей учётки — завершает все сессии, кроме текущей (чтобы не разлогиниться).
    Только для авторизованных админов."""
    admin = _get_admin_user(request)
    if not admin:
        return _unauthorized()

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return JSONResponse({"error": "Некорректный email"}, status_code=400)

    try:
        user = get_user_by_email(email)
        if not user:
            return JSONResponse({"error": "Пользователь не найден"}, status_code=404)

        is_self = email == admin.get("email", "")
        if is_self:
            current_token = request.cookies.get(AUTH_COOKIE_NAME)
            exclude_hash = hash_token(current_token) if current_token else None
            delete_user_sessions(user["id"], exclude_token_hash=exclude_hash)
            app_logger.info(f"[admin] Завершены чужие сессии {email} (текущая сохранена)")
        else:
            delete_user_sessions(user["id"])
            app_logger.info(f"[admin] Завершены все сессии {email}")

        return JSONResponse({"ok": True})
    except Exception as e:
        app_logger.error(f"[admin] Ошибка завершения сессий пользователя: {e}", exc_info=True)
        return JSONResponse({"error": "Internal error"}, status_code=500)


@router.post("/api/admin/test-email")
async def admin_test_email(request: Request):
    """Отправляет тестовое письмо всем администраторам. Только для авторизованных админов."""
    if not _get_admin_user(request):
        return _unauthorized()

    try:
        admin = _get_admin_user(request)
        sender_email = admin.get("email", "неизвестный") if admin else "неизвестный"

        recipients = collect_admin_emails()
        if not recipients:
            return JSONResponse({"error": "Нет адресов администраторов"}, status_code=400)

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        subject = "[tLib] Тестовое письмо"
        body = (
            f"Это тестовое письмо от системы уведомлений tLib.\n\n"
            f"Отправлено: {now_str}\n"
            f"Инициатор: {sender_email}\n"
            f"Получатели ({len(recipients)}): {', '.join(recipients)}\n\n"
            f"Если вы получили это письмо — SMTP настроен корректно."
        )
        send_admin_alert_direct(subject, body)
        app_logger.info(f"[admin] Тестовое письмо отправлено {len(recipients)} адм. по запросу {sender_email}")
        return JSONResponse({"ok": True, "recipients": len(recipients)})
    except Exception as e:
        app_logger.error(f"[admin] Ошибка отправки тестового письма: {e}", exc_info=True)
        return JSONResponse({"error": "Internal error"}, status_code=500)
