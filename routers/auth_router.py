# Version 1.4 - 21.06.2026 12:05:00 GMT
# Auth Router для TlibWebApp
# Описание: Magic link авторизация без пароля.
#           POST /api/auth/request-link — принять email + опциональный redirect, отправить magic link
#           GET  /auth/verify           — обработать клик из письма, выдать cookie, редирект
#           POST /api/auth/verify-code  — проверить цифровой код, выдать cookie (JSON)
#           POST /api/auth/logout       — удалить сессию, очистить cookie; ?all=1 — все сессии
#           GET  /api/auth/me           — вернуть данные текущего пользователя
# Изменения v1.2: гибридная авторизация — новый endpoint verify-code, _set_session_cookie helper.
# Изменения v1.3: logout поддерживает ?all=1 (выход со всех устройств через delete_all_sessions_for_token);
#           request-link: пер-IP троттлинг (request_throttle), дневной лимит писем (email_quota_remaining),
#           структурированные 429-ответы с reason и retry_after; EMAIL_QUOTA алерт при исчерпании лимита.
# Изменения v1.4: исчерпание дневного лимита логируется через log_email_quota_exceeded
#           (event_type=EMAIL_QUOTA), а не log_invalid_request (что давало event_type=INVALID_REQUEST).

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from config import (
    AUTH_COOKIE_NAME, AUTH_COOKIE_SECURE, AUTH_SESSION_MAX_AGE, ROOT_ADMIN_EMAIL,
    AUTH_EMAIL_DAILY_CAP,
)
from services.auth.auth_db import (
    check_magic_link_rate,
    create_magic_link,
    create_session,
    delete_session,
    delete_all_sessions_for_token,
    email_quota_remaining,
    bump_email_quota,
    seconds_until_utc_midnight,
    find_or_create_user,
    get_user_by_session,
    verify_magic_link,
    verify_magic_code,
)
from services.auth.email_service import send_magic_link
from services.auth.request_throttle import allow_request_link
from services.alerts.alerter import send_admin_alert
from logging_config import app_logger, security_logger

router = APIRouter(tags=["auth"])


def _set_session_cookie(response, session_token: str) -> None:
    """Устанавливает cookie сессии с едиными параметрами безопасности."""
    response.set_cookie(
        AUTH_COOKIE_NAME,
        session_token,
        max_age=AUTH_SESSION_MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=AUTH_COOKIE_SECURE,
    )


# ---------------------------------------------------------------------------
# POST /api/auth/request-link
# ---------------------------------------------------------------------------

@router.post("/api/auth/request-link")
async def request_link(request: Request):
    """
    Принимает {email, name?}, создаёт или находит пользователя,
    отправляет magic link с цифровым кодом.

    Порядок ограничений:
    1. Пер-IP троттлинг (request_throttle) — 5 запросов за 10 мин с одного IP.
    2. Валидация email.
    3. Пер-email rate limit — 1 запрос / 60 сек на email.
    4. Дневной лимит — не более AUTH_EMAIL_DAILY_CAP писем в сутки.
    На каждый лимит — свой 429 с reason и retry_after для UI.
    """
    client_ip = request.client.host if request.client else "unknown"

    # 1. Пер-IP троттлинг — намеренно ДО чтения тела запроса.
    # Боты с мусорными телами отклоняются без десериализации JSON;
    # опечатки в email с легитимного IP всё равно ограничены глобальным rate-limit (300/мин).
    allowed, retry_after_ip = allow_request_link(client_ip)
    if not allowed:
        mins = max(1, (retry_after_ip + 59) // 60)
        return JSONResponse(
            {
                "error": f"Слишком много запросов с вашего устройства. Попробуйте через ~{mins} мин.",
                "reason": "ip_throttle",
                "retry_after": retry_after_ip,
            },
            status_code=429,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    email    = (body.get("email")    or "").strip().lower()
    name     = (body.get("name")     or "").strip()
    redirect = (body.get("redirect") or "").strip()

    # 2. Валидация email
    if not email or "@" not in email:
        return JSONResponse({"error": "Некорректный email"}, status_code=400)

    # Validate redirect to prevent open redirect: must start with "/" and not contain "//"
    if redirect and (not redirect.startswith("/") or "//" in redirect):
        redirect = ""

    # 3. Пер-email rate limit (60 сек)
    if not check_magic_link_rate(email):
        return JSONResponse(
            {
                "error": "Ссылка уже отправлена на этот адрес. Повторите через ~1 мин.",
                "reason": "email_rate",
                "retry_after": 60,
            },
            status_code=429,
        )

    # 4. Дневной лимит исходящих писем
    if not email_quota_remaining(AUTH_EMAIL_DAILY_CAP):
        retry_after_day = seconds_until_utc_midnight()
        hours = max(1, retry_after_day // 3600)
        security_logger.log_email_quota_exceeded(client_ip, AUTH_EMAIL_DAILY_CAP)
        try:
            send_admin_alert("EMAIL_QUOTA", daily_cap=AUTH_EMAIL_DAILY_CAP)
        except Exception as _alert_err:
            app_logger.error(f"[auth] Не удалось отправить EMAIL_QUOTA алерт: {_alert_err}")
        return JSONResponse(
            {
                "error": (
                    f"Достигнут дневной лимит отправки писем. "
                    f"Попробуйте позже — лимит сбрасывается в 00:00 UTC (через ~{hours} ч)."
                ),
                "reason": "daily_cap",
                "retry_after": retry_after_day,
            },
            status_code=429,
        )

    user = find_or_create_user(email, name)
    if not user["is_active"]:
        return JSONResponse({"error": "Доступ заблокирован"}, status_code=403)

    token, code = create_magic_link(email)

    try:
        await asyncio.to_thread(send_magic_link, email, token, redirect, code)
    except Exception as e:
        app_logger.error(f"[auth] Failed to send magic link to {email}: {e}")
        return JSONResponse(
            {"error": "Не удалось отправить письмо. Попробуйте позже."},
            status_code=500
        )

    bump_email_quota()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# GET /auth/verify
# ---------------------------------------------------------------------------

@router.get("/auth/verify")
async def verify(request: Request):
    """
    Обрабатывает клик по ссылке из письма.
    При успехе: создаёт сессию, ставит cookie, редирект на redirect (или /login.html).
    При ошибке: редирект на redirect?error=expired (или /login.html?error=expired).
    """
    token    = request.query_params.get("token",    "").strip()
    redirect = request.query_params.get("redirect", "").strip()

    # Validate redirect: must start with "/" and not contain "//"
    if not redirect or not redirect.startswith("/") or "//" in redirect:
        redirect = "/login.html"

    # Strip any existing query string from redirect to safely append error param
    redirect_base = redirect.split("?")[0]

    if not token:
        return RedirectResponse(f"{redirect_base}?error=expired", status_code=302)

    email = verify_magic_link(token)
    if not email:
        return RedirectResponse(f"{redirect_base}?error=expired", status_code=302)

    user = find_or_create_user(email, "")
    if not user["is_active"]:
        return RedirectResponse(f"{redirect_base}?error=expired", status_code=302)

    ip = request.client.host if request.client else ""
    session_token = create_session(user["id"], ip)

    response = RedirectResponse(redirect, status_code=302)
    _set_session_cookie(response, session_token)
    app_logger.info(f"[auth] Session created for {email} from {ip}")
    return response


# ---------------------------------------------------------------------------
# POST /api/auth/verify-code
# ---------------------------------------------------------------------------

@router.post("/api/auth/verify-code")
async def verify_code(request: Request):
    """
    Проверяет 6-значный цифровой код, введённый пользователем.
    При успехе: создаёт сессию, ставит cookie, возвращает {"ok": true}.
    При ошибке: 401 {"error": "..."} — причина не раскрывается.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    email = (body.get("email") or "").strip().lower()
    code  = (body.get("code")  or "").strip()

    if not email or "@" not in email or not code:
        return JSONResponse({"error": "Некорректный запрос"}, status_code=400)

    verified_email = verify_magic_code(email, code)
    if not verified_email:
        return JSONResponse({"error": "Неверный или устаревший код"}, status_code=401)

    user = find_or_create_user(verified_email, "")
    if not user["is_active"]:
        return JSONResponse({"error": "Доступ заблокирован"}, status_code=403)

    ip = request.client.host if request.client else ""
    session_token = create_session(user["id"], ip)

    response = JSONResponse({"ok": True})
    _set_session_cookie(response, session_token)
    app_logger.info(f"[auth] Session created via code for {verified_email} from {ip}")
    return response


# ---------------------------------------------------------------------------
# POST /api/auth/logout
# ---------------------------------------------------------------------------

@router.post("/api/auth/logout")
async def logout(request: Request):
    """Удаляет сессию из БД, очищает cookie.

    ?all=1 — удаляет все сессии пользователя (выход со всех устройств).
    """
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if token:
        if request.query_params.get("all"):
            delete_all_sessions_for_token(token)
        else:
            delete_session(token)

    response = JSONResponse({"ok": True})
    response.delete_cookie(AUTH_COOKIE_NAME, samesite="strict", secure=AUTH_COOKIE_SECURE)
    return response


# ---------------------------------------------------------------------------
# GET /api/auth/me
# ---------------------------------------------------------------------------

@router.get("/api/auth/me")
async def me(request: Request):
    """
    Возвращает данные текущего пользователя по cookie.
    401 если не авторизован или сессия истекла.
    """
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    current_ip = request.client.host if request.client else ""
    user = get_user_by_session(token, current_ip)
    if not user:
        return JSONResponse({"error": "Session expired"}, status_code=401)

    role = user["role"]
    if ROOT_ADMIN_EMAIL and user["email"] == ROOT_ADMIN_EMAIL:
        role = "admin"

    return JSONResponse({
        "id":    user["id"],
        "email": user["email"],
        "name":  user["name"],
        "role":  role,
    })
