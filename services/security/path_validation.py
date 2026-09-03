# Version 1.0 - 08.01.2026 20:19:47 GMT
# Unified path validation helpers for TlibWebApp
# Описание: Единый модуль “источник правды” для проверки пользовательских путей.
#           Предоставляет:
#           - decode_url_path(): ограниченное декодирование URL (%xx) с защитой от double-encoding.
#           - validate_and_resolve_under_base(): возвращает уже проверенный Path внутри base_dir
#             (защита от Path Traversal, абсолютных путей, backslash, Windows drive/UNC).
#           - validate_zip_member_path(): string-only проверка путей внутри ZIP (без filesystem resolve).
#           Ошибки возвращаются как PathValidationError с status_code (400/403) и reason для логирования.

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from logging_config import security_logger
from config import URL_DECODE_MAX_ROUNDS


_WINDOWS_DRIVE_RE = re.compile(r"^[a-zA-Z]:")
_UNC_PREFIXES = ("\\\\", "//")


@dataclass(frozen=True)
class PathValidationError(Exception):
    """Контролируемая ошибка валидации пути (для роутеров)."""

    status_code: int
    message: str
    reason: str = "Invalid path"


def decode_url_path(path_str: str, *, max_rounds: int = URL_DECODE_MAX_ROUNDS) -> str:
    """
    Декодирует URL-encoded строку (%xx) ограниченное число раз.

    Зачем: Path Traversal может приходить double-encoded (%252e%252e%252f -> %2e%2e%2f -> ../).
    Мы декодируем до max_rounds (по умолчанию URL_DECODE_MAX_ROUNDS) и останавливаемся, когда строка стабилизировалась.
    """
    if not isinstance(path_str, str):
        return ""

    current = path_str
    for _ in range(max(0, int(max_rounds))):
        decoded = urllib.parse.unquote(current)
        if decoded == current:
            break
        current = decoded
    return current


def _log_and_raise(
    *,
    client_ip: str,
    endpoint: str,
    raw_input: str,
    status_code: int,
    reason: str,
    message: str,
) -> None:
    """
    Логирует событие безопасности и бросает PathValidationError.
    """
    try:
        # Для совместимости с существующей практикой логирования:
        # - path_traversal_attempt: более высокий приоритет
        # - invalid_request: для остальных причин
        if "traversal" in reason.lower():
            security_logger.log_path_traversal_attempt(client_ip, raw_input)
        else:
            security_logger.log_invalid_request(client_ip, endpoint, reason)
    except Exception:
        # Никогда не роняем приложение из-за логгера.
        pass
    raise PathValidationError(status_code=status_code, message=message, reason=reason)


def _reject_if_obviously_unsafe(
    decoded: str,
    *,
    client_ip: str,
    endpoint: str,
    raw_input: str,
    allow_slash: bool,
) -> None:
    """
    Быстрые отсекающие проверки на “вход точно плохой”.
    """
    # Null byte — частый трюк для обмана файловых API.
    if "\x00" in decoded:
        _log_and_raise(
            client_ip=client_ip,
            endpoint=endpoint,
            raw_input=raw_input,
            status_code=400,
            reason="Null byte in path",
            message="Invalid path",
        )

    # Запрещаем backslash, чтобы не допускать Windows-разделители и смешанные формы.
    if "\\" in decoded:
        _log_and_raise(
            client_ip=client_ip,
            endpoint=endpoint,
            raw_input=raw_input,
            status_code=400,
            reason="Backslash in path",
            message="Invalid path",
        )

    # Абсолютные пути (POSIX) и UNC пути.
    if decoded.startswith("/") or decoded.startswith(_UNC_PREFIXES):
        _log_and_raise(
            client_ip=client_ip,
            endpoint=endpoint,
            raw_input=raw_input,
            status_code=400,
            reason="Absolute path",
            message="Invalid path",
        )

    # Windows drive forms: C:, C:/..., etc.
    if _WINDOWS_DRIVE_RE.match(decoded):
        _log_and_raise(
            client_ip=client_ip,
            endpoint=endpoint,
            raw_input=raw_input,
            status_code=400,
            reason="Windows drive path",
            message="Invalid path",
        )

    # Path traversal patterns. Важно: НЕ запрещаем двойные точки в имени файла (document..pdf).
    # Запрещаем именно сегменты ".." / переходы между сегментами.
    if decoded.startswith(".."):
        _log_and_raise(
            client_ip=client_ip,
            endpoint=endpoint,
            raw_input=raw_input,
            status_code=400,
            reason="Path traversal attempt",
            message="Invalid path",
        )

    if allow_slash:
        if "../" in decoded:
            _log_and_raise(
                client_ip=client_ip,
                endpoint=endpoint,
                raw_input=raw_input,
                status_code=400,
                reason="Path traversal attempt",
                message="Invalid path",
            )
    else:
        # Для basename-only входов любые / недопустимы.
        if "/" in decoded:
            _log_and_raise(
                client_ip=client_ip,
                endpoint=endpoint,
                raw_input=raw_input,
                status_code=400,
                reason="Slash in filename",
                message="Invalid filename",
            )


def validate_zip_member_path(
    path_str: str,
    *,
    client_ip: str = "unknown",
    endpoint: str = "unknown",
    max_decode_rounds: int = URL_DECODE_MAX_ROUNDS,
) -> str:
    """
    Валидация пути файла ВНУТРИ ZIP архива (string-only, без filesystem resolve).

    Разрешает вложенные каталоги через '/' (как в ZIP), но запрещает:
    - traversal (../)
    - абсолютные пути
    - backslash
    - Windows drive / UNC
    """
    raw = path_str if isinstance(path_str, str) else ""
    decoded = decode_url_path(raw, max_rounds=max_decode_rounds)

    _reject_if_obviously_unsafe(
        decoded,
        client_ip=client_ip,
        endpoint=endpoint,
        raw_input=raw,
        allow_slash=True,
    )

    # Доп. строгая проверка сегментов: запрещаем сегмент ровно "..".
    parts = [p for p in decoded.split("/") if p != ""]
    if any(p == ".." for p in parts):
        _log_and_raise(
            client_ip=client_ip,
            endpoint=endpoint,
            raw_input=raw,
            status_code=400,
            reason="Path traversal attempt",
            message="Invalid path",
        )

    return decoded


def validate_and_resolve_under_base(
    base_dir: Path,
    user_path: str,
    *,
    client_ip: str = "unknown",
    endpoint: str = "unknown",
    require_basename: bool = False,
    allowed_suffixes: list[str] | tuple[str, ...] | None = None,
    max_decode_rounds: int = URL_DECODE_MAX_ROUNDS,
) -> Path:
    """
    Валидация filesystem пути и возврат “уже проверенного” Path внутри base_dir.

    - base_dir: директория-граница (все user_path должны быть внутри неё).
    - user_path: строка из URL/параметров (может быть URL-encoded).
    - require_basename: если True, запрещаем '/' и требуем basename (защита от передачи пути вместо имени файла).
    - allowed_suffixes: опциональный whitelist расширений (например ['.pdf']).
    """
    raw = user_path if isinstance(user_path, str) else ""
    decoded = decode_url_path(raw, max_rounds=max_decode_rounds)

    _reject_if_obviously_unsafe(
        decoded,
        client_ip=client_ip,
        endpoint=endpoint,
        raw_input=raw,
        allow_slash=not require_basename,
    )

    if require_basename:
        # Path(...).name == decoded гарантирует отсутствие каталогов и нормализацию basename.
        if Path(decoded).name != decoded:
            _log_and_raise(
                client_ip=client_ip,
                endpoint=endpoint,
                raw_input=raw,
                status_code=400,
                reason="Not a basename",
                message="Invalid filename",
            )

    if allowed_suffixes:
        decoded_l = decoded.lower()
        allowed_l = [str(s).lower() for s in allowed_suffixes if str(s)]
        if not any(decoded_l.endswith(suf) for suf in allowed_l):
            _log_and_raise(
                client_ip=client_ip,
                endpoint=endpoint,
                raw_input=raw,
                status_code=400,
                reason="Disallowed file extension",
                message="Invalid filename",
            )

    try:
        base_path = Path(base_dir).resolve()
    except Exception:
        _log_and_raise(
            client_ip=client_ip,
            endpoint=endpoint,
            raw_input=raw,
            status_code=500,
            reason="Base directory resolution failed",
            message="Internal server error",
        )

    try:
        full_path = (base_path / decoded).resolve()
    except Exception:
        _log_and_raise(
            client_ip=client_ip,
            endpoint=endpoint,
            raw_input=raw,
            status_code=400,
            reason="Path resolution failed (possible traversal)",
            message="Invalid path",
        )

    # Directory boundary check: строго через Path API, а не строковый startswith.
    try:
        full_path.relative_to(base_path)
    except Exception:
        _log_and_raise(
            client_ip=client_ip,
            endpoint=endpoint,
            raw_input=raw,
            status_code=403,
            reason="Path outside base directory",
            message="Access denied",
        )

    return full_path

