"""
Обработка отложенных уведомлений о публикации отчётов.

Вызывается из file_watcher_task.py в конце каждого цикла (вне db_lock):
    await asyncio.to_thread(process_pending_notifications)

Логика:
  - Ищет маркеры <PENDING_NOTIFY_DIRECTORY>/<ID>.notify.
  - Если data/<ID>.json существует — отчёт опубликован: шлёт письмо автору, удаляет маркер.
  - Если в 40_error есть <ID>.* файлы — обработка упала: шлёт письмо админам, удаляет маркер.
  - Иначе — ещё в очереди, маркер остаётся.
"""

import logging
from pathlib import Path
from urllib.parse import quote

from config import (
    DATA_DIRECTORY,
    PENDING_NOTIFY_DIRECTORY,
    SITE_URL,
    UPLOAD_ERROR_DIRECTORY,
)
from logging_config import app_logger
from services.alerts.recipients import collect_admin_emails
from services.auth.auth_db import get_user_by_id
from services.auth.email_service import send_processing_failed_notice, send_report_published
from services.json_io import read_json


def _build_report_url(report_json: dict) -> str:
    """Формирует ссылку вида {SITE_URL}/?<Шифр>-<ДопШифр> или {SITE_URL}/?<Шифр>."""
    shifr_raw = report_json.get("Шифр", "")
    dop = (report_json.get("ДопШифр") or "").strip()
    try:
        shifr_int = int(shifr_raw)
    except (TypeError, ValueError):
        shifr_int = None

    if shifr_int is not None:
        key = f"{shifr_int}-{dop}" if dop else str(shifr_int)
    else:
        key = f"{shifr_raw}-{dop}" if dop else str(shifr_raw)

    encoded = quote(key, safe="-_.~")
    base = SITE_URL.rstrip("/")
    return f"{base}/?{encoded}"


def process_pending_notifications() -> None:
    """
    Сверяет маркеры ожидающих уведомлений с реальным состоянием отчётов
    и отправляет письма. Выполняется синхронно (вызывается из asyncio.to_thread).
    """
    notify_dir = Path(PENDING_NOTIFY_DIRECTORY)
    if not notify_dir.exists():
        return

    data_dir = Path(DATA_DIRECTORY)
    error_dir = Path(UPLOAD_ERROR_DIRECTORY)

    for marker in sorted(notify_dir.glob("*.notify")):
        norm_id = marker.stem  # например «00010-TLIB»
        try:
            _process_one_marker(marker, norm_id, data_dir, error_dir)
        except Exception:
            app_logger.exception(f"[notify] Ошибка при обработке маркера {marker.name}")


def _process_one_marker(
    marker: Path,
    norm_id: str,
    data_dir: Path,
    error_dir: Path,
) -> None:
    published_json = data_dir / f"{norm_id}.json"

    if published_json.exists():
        _handle_published(marker, norm_id, published_json)
        return

    error_files = list(error_dir.glob(f"{norm_id}.*"))
    if error_files:
        _handle_failed(marker, norm_id, error_dir)
        return

    # Ещё в очереди (20_go / 30_processing / ожидает архив) — не трогаем
    app_logger.debug(f"[notify] {norm_id}: ещё в очереди, маркер оставлен")


def _handle_published(marker: Path, norm_id: str, published_json: Path) -> None:
    """Отчёт успешно опубликован в data/. Шлём письмо автору и удаляем маркер."""
    try:
        report = read_json(published_json)
    except Exception as e:
        app_logger.error(f"[notify] {norm_id}: не удалось прочитать JSON отчёта: {e}")
        return

    zagruzil_id_raw = report.get("ЗагрузилID")
    if zagruzil_id_raw is None:
        app_logger.warning(f"[notify] {norm_id}: ЗагрузилID отсутствует в JSON, маркер удалён без письма")
        marker.unlink(missing_ok=True)
        return

    try:
        uploader = get_user_by_id(int(zagruzil_id_raw))
    except (TypeError, ValueError) as e:
        app_logger.error(f"[notify] {norm_id}: некорректный ЗагрузилID={zagruzil_id_raw}: {e}")
        marker.unlink(missing_ok=True)
        return

    if not uploader or not uploader["email"]:
        app_logger.warning(f"[notify] {norm_id}: автор не найден или без email, маркер удалён без письма")
        marker.unlink(missing_ok=True)
        return

    uploader_email = uploader["email"]
    admin_comment = marker.read_text(encoding="utf-8")
    report_url = _build_report_url(report)

    try:
        send_report_published(uploader_email, norm_id, report_url, admin_comment, SITE_URL)
    except Exception as e:
        app_logger.error(
            f"[notify] {norm_id}: SMTP ошибка при отправке автору {uploader_email}, "
            f"маркер сохранён (повтор в следующем цикле): {e}"
        )
        return  # оставляем маркер для повторной попытки

    marker.unlink(missing_ok=True)
    app_logger.info(f"[notify] {norm_id}: письмо об публикации отправлено на {uploader_email}")


def _handle_failed(marker: Path, norm_id: str, error_dir: Path) -> None:
    """Обработка упала в 40_error. Шлём письмо администраторам и удаляем маркер."""
    err_file = error_dir / f"{norm_id}.err"
    error_text = ""
    if err_file.exists():
        try:
            error_text = err_file.read_text(encoding="utf-8")
        except Exception as e:
            app_logger.warning(f"[notify] {norm_id}: не удалось прочитать .err файл: {e}")

    admin_emails = collect_admin_emails()
    if not admin_emails:
        app_logger.warning(f"[notify] {norm_id}: нет адресов администраторов, маркер удалён без письма")
        marker.unlink(missing_ok=True)
        return

    try:
        send_processing_failed_notice(admin_emails, norm_id, error_text, SITE_URL)
    except Exception as e:
        app_logger.error(
            f"[notify] {norm_id}: SMTP ошибка при отправке админам, "
            f"маркер сохранён (повтор в следующем цикле): {e}"
        )
        return  # оставляем маркер для повторной попытки

    marker.unlink(missing_ok=True)
    app_logger.info(f"[notify] {norm_id}: письмо об ошибке обработки отправлено {len(admin_emails)} адм.")
