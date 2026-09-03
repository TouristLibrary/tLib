# Version 1.0 - 14.06.2026 11:32:00 GMT
# Auth session helpers для TlibWebApp
# Описание: Общие хелперы аутентификации и авторизации для upload_router и admin_router.
#           Выделены из роутеров для устранения дублирования кода.

from fastapi import Request
from fastapi.responses import JSONResponse

from config import AUTH_COOKIE_NAME, ROOT_ADMIN_EMAIL
from services.auth.auth_db import get_user_by_session


def get_current_user(request: Request) -> dict | None:
    """Возвращает текущего пользователя по cookie session_token или None."""
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return None
    current_ip = request.client.host if request.client else ""
    return get_user_by_session(token, current_ip)


def is_admin(user: dict) -> bool:
    """Проверяет, является ли пользователь администратором."""
    return bool(
        user
        and (
            user["role"] == "admin"
            or (ROOT_ADMIN_EMAIL and user["email"] == ROOT_ADMIN_EMAIL)
        )
    )


def get_admin_user(request: Request) -> dict | None:
    """Возвращает пользователя если он администратор, иначе None."""
    user = get_current_user(request)
    if not user:
        return None
    return user if is_admin(user) else None


def can_edit_report(user: dict | None, zagruzil_id) -> bool:
    """
    Проверяет право редактировать/удалять опубликованный отчёт.
    Разрешено администраторам и пользователю, который загрузил отчёт (ЗагрузилID).
    """
    if not user:
        return False
    if is_admin(user):
        return True
    if zagruzil_id is None:
        return False
    try:
        return int(zagruzil_id) == int(user["id"])
    except (TypeError, ValueError):
        return False


def unauthorized() -> JSONResponse:
    """HTTP 401 в едином формате."""
    return JSONResponse({"error": "Unauthorized"}, status_code=401)


def forbidden() -> JSONResponse:
    """HTTP 403 в едином формате."""
    return JSONResponse({"error": "Forbidden"}, status_code=403)
