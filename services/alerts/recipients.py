# Version 1.0 - 12.06.2026 14:00:00 GMT
# Сбор адресов администраторов для TlibWebApp
# Описание: Единая точка получения email-адресов всех активных администраторов.
#           Переиспользуется в alerter.py, digest.py и file_watcher/notify.py.

from config import ROOT_ADMIN_EMAIL
from services.auth.auth_db import get_admin_users


def collect_admin_emails() -> list[str]:
    """Возвращает отсортированный список уникальных email всех активных администраторов.

    Включает ROOT_ADMIN_EMAIL (из .env) и всех пользователей с ролью 'admin' в auth.db.
    Безопасен при пустой БД — ROOT_ADMIN_EMAIL всегда добавляется если задан.
    """
    emails: set[str] = set()
    if ROOT_ADMIN_EMAIL:
        emails.add(ROOT_ADMIN_EMAIL.strip().lower())
    try:
        for user in get_admin_users():
            if user.get("email"):
                emails.add(user["email"].strip().lower())
    except Exception:
        pass
    return sorted(emails)
