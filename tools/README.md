# tools/

Вспомогательные скрипты, **запускаемые вручную** администратором. Не входят в рантайм приложения.
Python-утилиты запускать из корня проекта, например: `python tools/manage_users.py list`.

## Пользователи (auth.db)

| Скрипт | Назначение |
|---|---|
| `manage_users.py` | CLI-управление пользователями auth.db (break-glass дополнение к web `/admin`). |
| `import_users_from_csv.py` | Импорт пользователей из CSV в auth.db. |
| `sync_user_names_from_reports.py` | Синхронизация `users.name` из поля `ЗагрузилИмя` последнего отчёта в tlib.db. |

## Диагностика БД и данных (read-only)

| Скрипт | Назначение |
|---|---|
| `analyze_marshrut_issues.py` | Анализ проблем поля «Маршрут» в tlib.db. |
| `diagnose_archive_mismatch.py` | Поиск расхождений между файлами в `data/` и таблицей tlib.db. |

## Обслуживание

| Скрипт | Назначение |
|---|---|
| `clean_trash.py` | Очистка регенерируемого мусора (`__pycache__/`, `.pytest_cache/`, файлы в `logs/`). Триггеры: «покажи мусор» / «очисти мусор». |
| `normalize_dopshifr_case.py` | Разовое приведение ДопШифр к верхнему регистру в именах файлов и в поле `"ДопШифр"` JSON в `data/`. Dry-run по умолчанию, `--apply` для записи. |
| `check_smtp_gmail.py` | Ручная проверка отправки email через Gmail SMTP. |

## Подготовка данных (офлайн, до заливки в File Watcher)

Автономные скрипты с диалогами `tkinter` / консольным вводом. Не импортируют пакеты проекта.

| Скрипт | Назначение | Зависимости |
|---|---|---|
| `ZipJsonCheck.py` | Расхождения наборов ZIP- и JSON-файлов по именам. | stdlib |
| `CheckZip.py` | Проверка целостности ZIP и категоризация по содержимому. | stdlib + tkinter |
| `PDFSizeStats.py` | Гистограмма и статистика размеров PDF (в т.ч. внутри ZIP). | stdlib + tkinter |
| `Zip2GPX.py` | Извлечение гео-файлов (`.gpx/.kml/.kmz/.plt/.geojson`) из ZIP. | stdlib + tkinter |
| `zipname2utf8.py` | Нормализация кодировок имён файлов в ZIP в UTF-8. Список кодировок синхронизирован с `config.ZIP_ENCODINGS`. | stdlib + tkinter |
| `LinearizedPDF.py` | Проверка и линеаризация PDF (Fast Web View). | QPDF / Ghostscript / pdftk (внешние) или `pikepdf`; опц. `PyPDF2` |
| `img2pdf.py` | Объединение изображений в один PDF. | `Pillow` |
| `testcyr.py` | Генерация файлов с кириллицей в разных кодировках (тест распознавания). | stdlib |

Опциональные зависимости ставятся по необходимости и **не входят** в основной `requirements.txt`:
`pip install Pillow pikepdf PyPDF2`.

## Развёртывание сервера (Ubuntu)

`install_ubuntu_common.sh`, `install_ubuntu_local.sh`, `install_ubuntu_caddy.sh`,
`install_ubuntu_tailscale_funnel.sh` — установка/настройка окружения; `VM-Static-IP.md` — заметка по статическому IP.
