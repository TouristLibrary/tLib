# Version 1.11 - 24.06.2026 23:00:00 GMT
# Auth DB layer для TlibWebApp
# Описание: SQLite-слой авторизации. Таблицы: users, magic_links, sessions, app_settings.
#           Все токены хранятся как SHA-256 хеши — raw токен не попадает в БД.
#           magic_links удаляются атомарно через DELETE...RETURNING (SQLite 3.35+).
# Изменения v1.1: гибридная авторизация — magic_links хранит code_hash и attempts;
#           create_magic_link возвращает (token, code), добавлена verify_magic_code.
# Изменения v1.2: добавлена таблица app_settings и функции get_setting/set_setting.
# Изменения v1.3: добавлены delete_all_sessions_for_token (выход со всех устройств);
#           email_quota_remaining, bump_email_quota, seconds_until_utc_midnight
#           (дневной лимит писем через app_settings).
# Изменения v1.4: AUTH_SCHEMA_SQL вынесен в модульную константу — единый источник
#           истины схемы для tools/import_users_from_csv.py.
# Изменения v1.5: исправлен запрос get_all_sessions — убран несуществующий столбец s.id.
# Изменения v1.6: get_all_users и get_all_sessions сортируются по email COLLATE NOCASE.
# Изменения v1.7: get_all_sessions + s.token_hash; hash_token (публичная обёртка); delete_session_by_hash.
# Изменения v1.8: поле last_login_at в users — проставляется в create_session(), миграция в init_auth_db().
# Изменения v1.9: get_all_sessions() сортируется по s.created_at DESC (недавние сверху).
# Изменения v1.10: поле last_login_ip в users — проставляется в create_session(), миграция в init_auth_db();
#           get_all_users() возвращает last_login_ip и active_session_count (только живые сессии);
#           delete_user_sessions() принимает exclude_token_hash для исключения текущей сессии;
#           удалены get_all_sessions() и delete_session_by_hash() — не используются после рефакторинга панели 7.
# Изменения v1.11: возвращена get_all_sessions() — ошибочно удалена в v1.10, используется tools/manage_users.py.

import hashlib
import ipaddress
import secrets
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import (AUTH_DB_PATH, AUTH_MAGIC_LINK_TTL, AUTH_MAGIC_LINK_RATE, AUTH_SESSION_MAX_AGE,
                    ROOT_ADMIN_EMAIL, AUTH_CODE_LENGTH, AUTH_CODE_MAX_ATTEMPTS)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _same_network(ip_a: str, ip_b: str) -> bool:
    """True, если оба IP в одной подсети (/24 IPv4, /64 IPv6).
    Пустые/непарсящиеся значения (например 'unknown') сравниваются строго."""
    if not ip_a or not ip_b:
        return ip_a == ip_b
    try:
        a = ipaddress.ip_address(ip_a)
        b = ipaddress.ip_address(ip_b)
    except ValueError:
        return ip_a == ip_b
    # Нормализация IPv4-mapped IPv6 (::ffff:x.x.x.x) к чистому IPv4,
    # чтобы один клиент в разных представлениях не считался разными версиями.
    # ipv4_mapped есть только у IPv6Address, поэтому через getattr.
    a = getattr(a, "ipv4_mapped", None) or a
    b = getattr(b, "ipv4_mapped", None) or b
    if a.version != b.version:
        return False
    prefix = 24 if a.version == 4 else 64
    return b in ipaddress.ip_network(f"{a}/{prefix}", strict=False)


def _expires_iso(ttl_seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _connect() -> sqlite3.Connection:
    Path(AUTH_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(AUTH_DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Схема БД (единый источник истины — используется также в tools/)
# ---------------------------------------------------------------------------

AUTH_SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS users (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        email         TEXT    NOT NULL UNIQUE,
        name          TEXT    NOT NULL DEFAULT '',
        role          TEXT    NOT NULL DEFAULT '',
        is_active     INTEGER NOT NULL DEFAULT 1,
        created_at    TEXT    NOT NULL,
        last_login_at TEXT,
        last_login_ip TEXT
    );

    CREATE TABLE IF NOT EXISTS magic_links (
        token_hash TEXT    PRIMARY KEY,
        code_hash  TEXT    NOT NULL,
        email      TEXT    NOT NULL,
        expires_at TEXT    NOT NULL,
        created_at TEXT    NOT NULL,
        attempts   INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        user_id    INTEGER NOT NULL REFERENCES users(id),
        expires_at TEXT    NOT NULL,
        created_at TEXT    NOT NULL,
        ip         TEXT    NOT NULL DEFAULT ''
    );

    CREATE INDEX IF NOT EXISTS idx_sessions_user    ON sessions(user_id);
    CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
    CREATE INDEX IF NOT EXISTS idx_magic_links_email ON magic_links(email);

    CREATE TABLE IF NOT EXISTS app_settings (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL DEFAULT ''
    );
"""

# ---------------------------------------------------------------------------
# Инициализация
# ---------------------------------------------------------------------------

def init_auth_db() -> None:
    """Создаёт таблицы при первом запуске. Безопасно вызывать при каждом старте.

    Миграция magic_links: если таблица существует без колонки code_hash (схема v1.0),
    дропаем и пересоздаём — записи эфемерны (TTL 15 мин), данных терять нечего.
    """
    with _connect() as conn:
        # Миграция: пересоздать magic_links при отсутствии колонки code_hash
        cols = {row[1] for row in conn.execute("PRAGMA table_info(magic_links)").fetchall()}
        if cols and "code_hash" not in cols:
            conn.execute("DROP TABLE magic_links")

        conn.executescript(AUTH_SCHEMA_SQL)

        ucols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "last_login_at" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT")
        if "last_login_ip" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN last_login_ip TEXT")


# ---------------------------------------------------------------------------
# Пользователи
# ---------------------------------------------------------------------------

def get_user_by_email(email: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def find_or_create_user(email: str, name: str) -> dict | None:
    """
    Возвращает существующего пользователя или создаёт нового.
    name устанавливается только при создании; если пустое — используется часть email до '@'.
    """
    user = get_user_by_email(email)
    if user:
        return user

    if not name:
        name = email.split("@")[0]

    with _connect() as conn:
        conn.execute(
            "INSERT INTO users (email, name, role, is_active, created_at) VALUES (?, ?, '', 1, ?)",
            (email, name, _now_iso())
        )
    return get_user_by_email(email)


def get_admin_users() -> list[dict]:
    """Возвращает всех активных пользователей с ролью 'admin'."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT email, name FROM users WHERE role = 'admin' AND is_active = 1 ORDER BY email"
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_users() -> list[dict]:
    now = _now_iso()
    with _connect() as conn:
        rows = conn.execute(
            """SELECT u.id, u.email, u.name, u.role, u.is_active, u.created_at,
                      u.last_login_at, u.last_login_ip,
                      (SELECT COUNT(*) FROM sessions s
                       WHERE s.user_id = u.id AND s.expires_at > ?) AS active_session_count
               FROM users u ORDER BY u.email COLLATE NOCASE""",
            (now,)
        ).fetchall()
    return [dict(r) for r in rows]


def set_user_active(email: str, is_active: int) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE users SET is_active = ? WHERE email = ?", (is_active, email)
        )
    return cur.rowcount > 0


def delete_user(email: str) -> bool:
    with _connect() as conn:
        user = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if not user:
            return False
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
        conn.execute("DELETE FROM users WHERE email = ?", (email,))
    return True


def set_user_role(email: str, role: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE users SET role = ? WHERE email = ?", (role, email)
        )
    return cur.rowcount > 0


def update_user_name(user_id: int, name: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE users SET name = ? WHERE id = ?", (name, user_id)
        )
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Magic links
# ---------------------------------------------------------------------------

def check_magic_link_rate(email: str) -> bool:
    """Возвращает True, если можно отправить ссылку (не слишком рано после предыдущей)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=AUTH_MAGIC_LINK_RATE)).isoformat()
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM magic_links WHERE email = ? AND created_at > ?",
            (email, cutoff)
        ).fetchone()
    return row is None


def create_magic_link(email: str) -> tuple[str, str]:
    """Создаёт magic link + цифровой код, сохраняет хеши в БД.

    Перед созданием удаляет все предыдущие magic links для этого email —
    активен только последний код.

    Возвращает кортеж (raw_token, raw_code) для передачи в письмо.
    """
    token = secrets.token_urlsafe(32)
    code  = f"{secrets.randbelow(10 ** AUTH_CODE_LENGTH):0{AUTH_CODE_LENGTH}d}"
    with _connect() as conn:
        conn.execute("DELETE FROM magic_links WHERE email = ?", (email,))
        conn.execute(
            "INSERT INTO magic_links (token_hash, code_hash, email, expires_at, created_at, attempts)"
            " VALUES (?, ?, ?, ?, ?, 0)",
            (_hash(token), _hash(code), email, _expires_iso(AUTH_MAGIC_LINK_TTL), _now_iso())
        )
    return token, code


def verify_magic_link(token: str) -> str | None:
    """
    Атомарно проверяет и удаляет magic link.
    Возвращает email если токен валиден и не истёк, иначе None.
    """
    with _connect() as conn:
        row = conn.execute(
            "DELETE FROM magic_links WHERE token_hash = ? AND expires_at > ? RETURNING email",
            (_hash(token), _now_iso())
        ).fetchone()
    return row["email"] if row else None


def verify_magic_code(email: str, code: str) -> str | None:
    """Проверяет цифровой код авторизации для указанного email.

    При верном коде: атомарно удаляет запись, возвращает email.
    При неверном: увеличивает счётчик попыток; при достижении AUTH_CODE_MAX_ATTEMPTS
    удаляет запись (защита от перебора). Возвращает None.
    Истёкшая или отсутствующая запись → None.
    """
    now = _now_iso()
    with _connect() as conn:
        row = conn.execute(
            "SELECT token_hash, code_hash, attempts FROM magic_links"
            " WHERE email = ? AND expires_at > ?",
            (email, now)
        ).fetchone()
        if not row:
            return None
        if row["code_hash"] == _hash(code):
            conn.execute(
                "DELETE FROM magic_links WHERE token_hash = ?", (row["token_hash"],)
            )
            return email
        new_attempts = row["attempts"] + 1
        if new_attempts >= AUTH_CODE_MAX_ATTEMPTS:
            conn.execute(
                "DELETE FROM magic_links WHERE token_hash = ?", (row["token_hash"],)
            )
        else:
            conn.execute(
                "UPDATE magic_links SET attempts = ? WHERE token_hash = ?",
                (new_attempts, row["token_hash"])
            )
        return None


# ---------------------------------------------------------------------------
# Сессии
# ---------------------------------------------------------------------------

def create_session(user_id: int, ip: str = "") -> str:
    """Создаёт сессию и фиксирует время и IP последнего входа. Возвращает raw token для cookie."""
    token = secrets.token_urlsafe(32)
    now = _now_iso()
    ip = ip or ""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at, created_at, ip) VALUES (?, ?, ?, ?, ?)",
            (_hash(token), user_id, _expires_iso(AUTH_SESSION_MAX_AGE), now, ip)
        )
        conn.execute(
            "UPDATE users SET last_login_at = ?, last_login_ip = ? WHERE id = ?",
            (now, ip, user_id)
        )
    return token


def get_user_by_session(token: str, current_ip: str | None = None) -> dict | None:
    """Проверяет сессию по cookie-токену, возвращает данные пользователя или None.

    Для администраторов дополнительно проверяет, что запрос пришёл из той же
    подсети (/24 IPv4, /64 IPv6), что и при создании сессии. При несовпадении
    возвращает None (форсит повторный вход). Обычных пользователей не затрагивает.
    """
    with _connect() as conn:
        row = conn.execute(
            """SELECT u.id, u.email, u.name, u.role, s.ip
               FROM sessions s
               JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.expires_at > ? AND u.is_active = 1""",
            (_hash(token), _now_iso())
        ).fetchone()
    if not row:
        return None
    is_admin = (row["role"] == "admin") or bool(ROOT_ADMIN_EMAIL and row["email"] == ROOT_ADMIN_EMAIL)
    if is_admin and current_ip is not None and not _same_network(row["ip"] or "", current_ip):
        return None
    return {"id": row["id"], "email": row["email"], "name": row["name"], "role": row["role"]}


def delete_session(token: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))


def delete_all_sessions_for_token(token: str) -> bool:
    """Удаляет все сессии пользователя по одному из его токенов (выход со всех устройств).

    Возвращает True если сессия по токену найдена и все сессии пользователя удалены,
    False если токен не найден (cookie невалидна).
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_id FROM sessions WHERE token_hash = ?", (_hash(token),)
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))
    return True


def delete_user_sessions(user_id: int, exclude_token_hash: str | None = None) -> None:
    """Удаляет все сессии пользователя; при exclude_token_hash пропускает указанную сессию."""
    with _connect() as conn:
        if exclude_token_hash:
            conn.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                (user_id, exclude_token_hash)
            )
        else:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def get_all_sessions() -> list[sqlite3.Row]:
    """Возвращает все активные сессии с данными пользователя. Используется tools/manage_users.py."""
    with _connect() as conn:
        return conn.execute(
            """SELECT s.token_hash, u.email, u.name, s.ip, s.created_at, s.expires_at
               FROM sessions s
               JOIN users u ON u.id = s.user_id
               ORDER BY s.created_at DESC"""
        ).fetchall()


def hash_token(token: str) -> str:
    """Публичная обёртка для хеширования токена (SHA-256)."""
    return _hash(token)


# ---------------------------------------------------------------------------
# Настройки приложения
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    """Возвращает значение настройки по ключу или default если не задана."""
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default
    except Exception:
        return default


def set_setting(key: str, value: str) -> None:
    """Сохраняет значение настройки. Создаёт запись если не существует."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value)
        )


# ---------------------------------------------------------------------------
# Очистка просроченных записей
# ---------------------------------------------------------------------------

def cleanup_expired() -> None:
    """Удаляет просроченные magic links и сессии. Вызывается из фоновой задачи."""
    now = _now_iso()
    with _connect() as conn:
        conn.execute("DELETE FROM magic_links WHERE expires_at < ?", (now,))
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))


# ---------------------------------------------------------------------------
# Дневной лимит исходящих писем (анти-абуз request-link)
# ---------------------------------------------------------------------------

def seconds_until_utc_midnight() -> int:
    """Возвращает число секунд до следующей полуночи UTC (для retry_after в ответе)."""
    now = datetime.now(timezone.utc)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((midnight - now).total_seconds()))


def email_quota_remaining(cap: int) -> bool:
    """Возвращает True, если дневной бюджет исходящих писем ещё не исчерпан.

    Счётчик хранится в app_settings под ключами 'email_quota_date' (дата UTC ISO)
    и 'email_quota_count' (строка-число). При смене даты UTC сбрасывается автоматически.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    with _connect() as conn:
        date_row = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'email_quota_date'"
        ).fetchone()
        count_row = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'email_quota_count'"
        ).fetchone()

    stored_date = date_row["value"] if date_row else ""
    if stored_date != today:
        return True

    try:
        count = int(count_row["value"]) if count_row else 0
    except (ValueError, TypeError):
        count = 0

    return count < cap


def bump_email_quota() -> None:
    """Инкрементирует счётчик дневных исходящих писем.

    Вызывается ПОСЛЕ успешной отправки, чтобы сбой SMTP не блокировал вход.
    Чтение + запись в одной транзакции; минимальный овершут при гонке допустим.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    with _connect() as conn:
        date_row = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'email_quota_date'"
        ).fetchone()
        count_row = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'email_quota_count'"
        ).fetchone()

        stored_date = date_row["value"] if date_row else ""
        if stored_date != today:
            new_count = 1
        else:
            try:
                new_count = (int(count_row["value"]) if count_row else 0) + 1
            except (ValueError, TypeError):
                new_count = 1

        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ('email_quota_date', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (today,)
        )
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES ('email_quota_count', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(new_count),)
        )
