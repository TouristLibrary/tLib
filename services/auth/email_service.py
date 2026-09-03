# Version 1.3 - 14.06.2026 13:40:00 GMT
# Email service для TlibWebApp
# Описание: Отправка magic link по email через Gmail SMTP (smtplib).
#           Credentials читаются из config (загружаются из data.secret/.env).
#           Паттерн отправки идентичен tests/test_smtp_gmail.py.
#           redirect — необязательный параметр: куда вернуть пользователя после верификации.
# Изменения v1.2: send_magic_link принимает code и включает его в текст письма
#           (гибридная авторизация: ссылка + цифровой код).
# 1.3: send_report_decision() — удалён параметр published и мёртвая ветка «опубликован»
#      (публикационные письма отправляются через send_report_published в notify.py).

import smtplib
from email.mime.text import MIMEText
from urllib.parse import quote

from config import SMTP_SERVER, SMTP_PORT, SMTP_SENDER, SMTP_PASSWORD, SITE_URL
from logging_config import app_logger


def send_magic_link(email: str, token: str, redirect: str = "", code: str = "") -> None:
    """
    Отправляет письмо с magic link и цифровым кодом на указанный email.
    Вызывается из asyncio.to_thread() чтобы не блокировать event loop.

    redirect — опциональный путь для редиректа после верификации (например "/admin").
               Если пустой — верификация редиректит на /login.html.
    code     — 6-значный цифровой код для ввода на другом устройстве.

    Raises:
        Exception: если SMTP недоступен или credentials неверные
    """
    if not SMTP_SENDER or not SMTP_PASSWORD:
        raise RuntimeError("SMTP credentials not configured (check data.secret/.env)")
    if not SITE_URL:
        raise RuntimeError("SITE_URL not configured (check data.secret/.env)")

    link = f"{SITE_URL}/auth/verify?token={token}"
    if redirect:
        link += f"&redirect={quote(redirect, safe='')}"

    code_section = (
        f"\nЕсли вы открыли почту на другом устройстве — введите код на странице входа:\n\n"
        f"    {code}\n\n"
    ) if code else ""

    body = (
        f"Для входа на tLib перейдите по ссылке:\n\n"
        f"{link}\n"
        f"{code_section}"
        f"Ссылка и код действительны 15 минут и могут быть использованы только один раз.\n\n"
        f"Если вы не запрашивали вход — просто проигнорируйте это письмо."
    )

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"]    = SMTP_SENDER
    msg["To"]      = email
    msg["Subject"] = "Вход на tLib"

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as srv:
        srv.starttls()
        srv.login(SMTP_SENDER, SMTP_PASSWORD)
        srv.sendmail(SMTP_SENDER, email, msg.as_string())

    app_logger.info(f"[auth] Magic link sent to {email}")


def send_new_report_notice(admin_emails: list[str], report_id: str, uploader_name: str,
                           site_url: str, is_edit: bool = False) -> None:
    """
    Уведомляет всех администраторов о новом отчёте (или его правке), ожидающем рассмотрения.
    is_edit=True — текст и тема меняются на «отредактированный отчёт».
    Вызывается из asyncio.to_thread().

    Raises:
        Exception: если SMTP недоступен или credentials неверные
    """
    if not SMTP_SENDER or not SMTP_PASSWORD:
        raise RuntimeError("SMTP credentials not configured (check data.secret/.env)")
    if not site_url:
        raise RuntimeError("SITE_URL not configured (check data.secret/.env)")
    if not admin_emails:
        return

    if is_edit:
        subject = f"Отредактированный отчёт {report_id} ожидает рассмотрения"
        body = (
            f"Пользователь «{uploader_name}» отредактировал отчёт {report_id}.\n"
            f"Изменения ожидают рассмотрения библиотекарями.\n\n"
            f"Для рассмотрения перейдите на страницу загрузки:\n"
            f"{site_url}/upload.html\n"
        )
    else:
        subject = "Загружен новый отчет"
        body = (
            f"Загружен новый отчёт {report_id} от пользователя «{uploader_name}».\n\n"
            f"Для рассмотрения перейдите на страницу загрузки:\n"
            f"{site_url}/upload.html\n"
        )

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"]    = SMTP_SENDER
    msg["To"]      = ", ".join(admin_emails)
    msg["Subject"] = subject

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as srv:
        srv.starttls()
        srv.login(SMTP_SENDER, SMTP_PASSWORD)
        srv.sendmail(SMTP_SENDER, admin_emails, msg.as_string())

    action = "edit notice" if is_edit else "new report notice"
    app_logger.info(f"[upload] {action} sent to {len(admin_emails)} admin(s) for {report_id}")


def send_report_decision(uploader_email: str, report_id: str,
                         admin_comment: str, site_url: str) -> None:
    """
    Уведомляет загрузившего отчёт об отклонении заявки.
    Вызывается из asyncio.to_thread().
    Публикационные письма отправляются через send_report_published (notify.py).

    Raises:
        Exception: если SMTP недоступен или credentials неверные
    """
    if not SMTP_SENDER or not SMTP_PASSWORD:
        raise RuntimeError("SMTP credentials not configured (check data.secret/.env)")
    if not site_url:
        raise RuntimeError("SITE_URL not configured (check data.secret/.env)")
    if not uploader_email:
        return

    subject = f"Отчёт {report_id} отклонён"
    action_line = f"Ваш отчёт {report_id} был отклонён библиотекарями."

    comment_section = ""
    if admin_comment and admin_comment.strip():
        comment_section = f"\nКомментарий библиотекаря:\n{admin_comment.strip()}\n"

    body = f"{action_line}\n{comment_section}"

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"]    = SMTP_SENDER
    msg["To"]      = uploader_email
    msg["Subject"] = subject

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as srv:
        srv.starttls()
        srv.login(SMTP_SENDER, SMTP_PASSWORD)
        srv.sendmail(SMTP_SENDER, [uploader_email], msg.as_string())

    app_logger.info(f"[upload] Report decision (отклонён) sent to {uploader_email} for {report_id}")


def send_report_published(uploader_email: str, report_id: str, report_url: str,
                          admin_comment: str, site_url: str) -> None:
    """
    Уведомляет загрузившего отчёт о том, что отчёт реально опубликован и доступен на сайте.
    Отправляется из File Watcher после успешного завершения pipeline.
    Вызывается из asyncio.to_thread().

    Raises:
        Exception: если SMTP недоступен или credentials неверные
    """
    if not SMTP_SENDER or not SMTP_PASSWORD:
        raise RuntimeError("SMTP credentials not configured (check data.secret/.env)")
    if not uploader_email:
        return

    comment_section = ""
    if admin_comment and admin_comment.strip():
        comment_section = f"\nКомментарий библиотекаря:\n{admin_comment.strip()}\n"

    body = (
        f"Ваш отчёт {report_id} опубликован библиотекарями и теперь доступен на сайте.\n\n"
        f"Ссылка на отчёт:\n{report_url}\n"
        f"{comment_section}"
    )

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"]    = SMTP_SENDER
    msg["To"]      = uploader_email
    msg["Subject"] = f"Отчёт {report_id} опубликован"

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as srv:
        srv.starttls()
        srv.login(SMTP_SENDER, SMTP_PASSWORD)
        srv.sendmail(SMTP_SENDER, [uploader_email], msg.as_string())

    app_logger.info(f"[upload] Published notification sent to {uploader_email} for {report_id}")


def send_processing_failed_notice(admin_emails: list[str], report_id: str,
                                  error_text: str, site_url: str) -> None:
    """
    Уведомляет администраторов о том, что отчёт не прошёл обработку pipeline (упал в 40_error).
    Вызывается из asyncio.to_thread().

    Raises:
        Exception: если SMTP недоступен или credentials неверные
    """
    if not SMTP_SENDER or not SMTP_PASSWORD:
        raise RuntimeError("SMTP credentials not configured (check data.secret/.env)")
    if not admin_emails:
        return

    error_section = ""
    if error_text and error_text.strip():
        error_section = f"\nДетали ошибки:\n{error_text.strip()[:2000]}\n"

    body = (
        f"Отчёт {report_id} был направлен на публикацию, но не прошёл обработку.\n"
        f"Файлы перемещены в папку ошибок (40_error).{error_section}\n"
        f"Страница загрузки:\n{site_url}/upload.html\n"
    )

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"]    = SMTP_SENDER
    msg["To"]      = ", ".join(admin_emails)
    msg["Subject"] = f"Ошибка обработки отчёта {report_id}"

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as srv:
        srv.starttls()
        srv.login(SMTP_SENDER, SMTP_PASSWORD)
        srv.sendmail(SMTP_SENDER, admin_emails, msg.as_string())

    app_logger.info(f"[upload] Processing failure notice sent to {len(admin_emails)} admin(s) for {report_id}")


def send_delete_decision(requester_email: str, report_id: str, confirmed: bool,
                         admin_comment: str, site_url: str) -> None:
    """
    Уведомляет инициатора запроса об итоге рассмотрения удаления отчёта.
    confirmed=True  → отчёт удалён.
    confirmed=False → запрос отклонён, отчёт остаётся в библиотеке.
    Вызывается из asyncio.to_thread().

    Raises:
        Exception: если SMTP недоступен или credentials неверные
    """
    if not SMTP_SENDER or not SMTP_PASSWORD:
        raise RuntimeError("SMTP credentials not configured (check data.secret/.env)")
    if not requester_email:
        return

    if confirmed:
        subject = f"Отчёт {report_id} удалён из библиотеки"
        action_line = f"Ваш запрос на удаление отчёта {report_id} подтверждён, отчёт удалён из библиотеки."
    else:
        subject = f"Запрос на удаление отчёта {report_id} отклонён"
        action_line = f"Ваш запрос на удаление отчёта {report_id} отклонён, отчёт остаётся в библиотеке."

    comment_section = ""
    if admin_comment and admin_comment.strip():
        comment_section = f"\nКомментарий библиотекаря:\n{admin_comment.strip()}\n"

    body = f"{action_line}\n{comment_section}"

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"]    = SMTP_SENDER
    msg["To"]      = requester_email
    msg["Subject"] = subject

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as srv:
        srv.starttls()
        srv.login(SMTP_SENDER, SMTP_PASSWORD)
        srv.sendmail(SMTP_SENDER, [requester_email], msg.as_string())

    outcome = "confirmed" if confirmed else "rejected"
    app_logger.info(f"[upload] Delete decision ({outcome}) sent to {requester_email} for {report_id}")


def send_delete_request_notice(admin_emails: list[str], report_id: str,
                                requester: str, site_url: str,
                                reason: str = "") -> None:
    """
    Уведомляет администраторов о запросе на удаление опубликованного отчёта.
    Вызывается из asyncio.to_thread().

    Raises:
        Exception: если SMTP недоступен или credentials неверные
    """
    if not SMTP_SENDER or not SMTP_PASSWORD:
        raise RuntimeError("SMTP credentials not configured (check data.secret/.env)")
    if not admin_emails:
        return

    reason_section = f"\nПричина удаления:\n{reason.strip()}\n" if reason and reason.strip() else ""
    body = (
        f"Пользователь «{requester}» запросил удаление отчёта {report_id} из библиотеки.\n"
        f"{reason_section}\n"
        f"Для подтверждения или отклонения запроса перейдите на страницу загрузки:\n"
        f"{site_url}/upload.html\n"
    )

    msg = MIMEText(body, "plain", "utf-8")
    msg["From"]    = SMTP_SENDER
    msg["To"]      = ", ".join(admin_emails)
    msg["Subject"] = f"Запрос на удаление отчёта {report_id}"

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=15) as srv:
        srv.starttls()
        srv.login(SMTP_SENDER, SMTP_PASSWORD)
        srv.sendmail(SMTP_SENDER, admin_emails, msg.as_string())

    app_logger.info(f"[upload] Delete request notice sent to {len(admin_emails)} admin(s) for {report_id}")
