#!/usr/bin/env python3
# Version 1.0 - 22.06.2026 11:00:00 GMT
# Утилита CLI-управления пользователями auth.db (break-glass дополнение к web /admin).
"""
Утилита управления пользователями tLib.

Запуск из корня проекта:
    python tools/manage_users.py list
    python tools/manage_users.py deactivate user@example.com
    python tools/manage_users.py activate user@example.com
    python tools/manage_users.py delete user@example.com
    python tools/manage_users.py sessions
    python tools/manage_users.py grant user@example.com admin
    python tools/manage_users.py revoke user@example.com admin
"""

import sys
import os

# Добавляем корень проекта в sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Загружаем .env до импорта config
from dotenv import load_dotenv
load_dotenv("data.secret/.env")

from services.auth.auth_db import (
    init_auth_db,
    get_all_users,
    get_all_sessions,
    set_user_active,
    delete_user,
    delete_user_sessions,
    set_user_role,
    get_user_by_email,
)


def cmd_list():
    users = get_all_users()
    if not users:
        print("Пользователей нет.")
        return
    print(f"{'ID':<5} {'Активен':<8} {'Роль':<10} {'Имя':<20} {'Email':<35} {'Создан'}")
    print("-" * 95)
    for u in users:
        active  = "да" if u["is_active"] else "нет"
        role    = u["role"] or "—"
        name    = (u["name"] or "—")[:18]
        created = (u["created_at"] or "")[:16].replace("T", " ")
        print(f"{u['id']:<5} {active:<8} {role:<10} {name:<20} {u['email']:<35} {created}")


def cmd_sessions():
    sessions = get_all_sessions()
    if not sessions:
        print("Активных сессий нет.")
        return
    print(f"{'Email':<35} {'Имя':<20} {'IP':<16} {'Создана':<18} {'Истекает'}")
    print("-" * 105)
    for s in sessions:
        name    = (s["name"] or "—")[:18]
        ip      = (s["ip"] or "—")[:14]
        created = (s["created_at"] or "")[:16].replace("T", " ")
        expires = (s["expires_at"] or "")[:16].replace("T", " ")
        print(f"{s['email']:<35} {name:<20} {ip:<16} {created:<18} {expires}")


def cmd_deactivate(email: str):
    user = get_user_by_email(email)
    if not user:
        print(f"Пользователь {email} не найден.")
        return
    set_user_active(email, 0)
    delete_user_sessions(user["id"])
    print(f"Пользователь {email} заблокирован, все сессии удалены.")


def cmd_activate(email: str):
    if not get_user_by_email(email):
        print(f"Пользователь {email} не найден.")
        return
    set_user_active(email, 1)
    print(f"Пользователь {email} разблокирован.")


def cmd_delete(email: str):
    if delete_user(email):
        print(f"Пользователь {email} и все его сессии удалены.")
    else:
        print(f"Пользователь {email} не найден.")


def cmd_grant(email: str, role: str):
    if not get_user_by_email(email):
        print(f"Пользователь {email} не найден.")
        return
    set_user_role(email, role)
    print(f"Пользователю {email} назначена роль '{role}'.")


def cmd_revoke(email: str, role: str):
    user = get_user_by_email(email)
    if not user:
        print(f"Пользователь {email} не найден.")
        return
    if user["role"] != role:
        print(f"У пользователя {email} нет роли '{role}' (текущая роль: '{user['role'] or 'нет'}').")
        return
    set_user_role(email, "")
    print(f"Роль '{role}' снята с пользователя {email}.")


def usage():
    print(__doc__)
    sys.exit(1)


if __name__ == "__main__":
    init_auth_db()

    args = sys.argv[1:]
    if not args:
        usage()

    cmd = args[0]

    if cmd == "list":
        cmd_list()
    elif cmd == "sessions":
        cmd_sessions()
    elif cmd == "deactivate" and len(args) == 2:
        cmd_deactivate(args[1])
    elif cmd == "activate" and len(args) == 2:
        cmd_activate(args[1])
    elif cmd == "delete" and len(args) == 2:
        cmd_delete(args[1])
    elif cmd == "grant" and len(args) == 3:
        cmd_grant(args[1], args[2])
    elif cmd == "revoke" and len(args) == 3:
        cmd_revoke(args[1], args[2])
    else:
        usage()
