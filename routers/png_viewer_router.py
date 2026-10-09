# Version 3.0 - 09.10.2026 10:20:00 GMT
# PNG Viewer Router для TlibWebApp
# Описание: API endpoints для PNG viewer. Предоставляет листинг PNG директорий в data.cache
#           и списки PNG файлов для просмотра. Используется embedded-вьюером /png-viewer.
#           Логика resolve переехала в единый cache_router POST /resolve.
# 3.0: rendering=false, если докрутка не может стартовать: нет или невалидна meta, запись не partial,
#      исходника нет (resumable_pdf_entry). Иначе вьюер опрашивал бы /pages, когда рисовать не из чего.
# 2.9: поле rendering в ответе /pages — сервер рисует для want: идёт конвертация архива или нужна
#      докрутка (cache_watch.needs_resume, гистерезис resume_pdf_conversion). Вьюер опрашивает, только
#      пока rendering=true, иначе — при смене страницы: забытая вкладка не шлёт heartbeat всю ночь.
# 2.8: long-poll /pages — страницы want нет на диске частичной директории: запрос ждёт её до
#      PNG_PAGES_WAIT_SECONDS и отвечает, как только файл появился. Докрутка стартует до ожидания
#      (asyncio.create_task), а не после ответа (BackgroundTasks). «PDF page wait» — одна строка
#      на запрос с waited_ms и ready.
# 2.7: docstring /pages — темп опроса 1–2 с и дозаполнение страниц позади want (код не менялся).
# 2.6: INFO «PDF page wait» — страницы want нет на диске частичной директории, читатель видит заглушку;
#      converting (PDF, который сейчас рендерится) показывает причину ожидания.
# 2.5: рендер PDF по окну просмотра — /pages единственный триггер докрутки; решение «докручивать ли»
#      (гистерезис по окну) — в resume_pdf_conversion, которая больше не учитывает «В кэш».
# 2.4: имя архива и путь директории для heartbeat/докрутки берутся из проверенного full_path.
# 2.3: /pages обновляет mtime папки архива (touch_watch) — LRU не вытесняет читаемый PDF.
# 2.2: /pages — heartbeat просмотра для конвертации PDF, пока смотрят: пишет _watch.json
#      (параметр want — страница, которую показывает вьюер) и возобновляет частичную
#      конвертацию (resume_pdf_conversion), если она на паузе.
# 2.1: /pages переведён на канонический validate_and_resolve_under_base() (§3);
#      _is_safe_dirname и ручная startswith-проверка удалены;
#      _list_png_files использует .resolve() базы для корректного relative_to.

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

# Импорт конфигурации
from config import (
    CACHE_DIRECTORY,
    CACHE_URL_PATH,
    CACHE_META_FILENAME,
    PNG_PAGES_WAIT_SECONDS,
    PNG_PAGES_WAIT_STEP_SECONDS,
)

# Импорт сервисов кеша: heartbeat просмотра и докрутка частичной конвертации
from services.cache.cache_watch import touch_watch, read_pages_total, is_partial, count_pngs, needs_resume
from services.cache.cache_prepare_service import (
    is_preparing, read_prepare_status, resume_pdf_conversion, resumable_pdf_entry,
)
from services.cache.cache_service import generate_png_filename

# Импорт канонического валидатора путей
from services.security.path_validation import (
    validate_and_resolve_under_base,
    PathValidationError,
)

# Импорт логгеров
from logging_config import app_logger, log_with_data

# Создаем роутер
router = APIRouter(prefix="/api/png", tags=["png-viewer"])

# Запущенные из /pages докрутки: event loop держит на задачи только слабые ссылки, без сильной
# задачу мог бы собрать GC посреди рендера. Исключения resume_pdf_conversion ловит сама
_resume_tasks: set[asyncio.Task] = set()


def _scan_png_directories() -> list[dict]:
    """
    Сканирует data.cache на наличие PNG директорий.

    Читает _meta.json каждого архива вместо рекурсивного обхода файловой системы.
    Fallback на filesystem-scan для архивов без _meta.json.

    Returns:
        Список словарей с информацией о директориях:
        [{"name": "12345-ABC-png", "path": "12345-ABC/dir1/report-png", "page_count": 47}, ...]
    """
    cache_dir = Path(CACHE_DIRECTORY)

    if not cache_dir.exists():
        return []

    png_dirs = []

    for folder_path in cache_dir.iterdir():
        if not folder_path.is_dir():
            continue

        meta_path = folder_path / CACHE_META_FILENAME
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            for f in meta.get("files", []):
                if f.get("kind") != "pdf" or "png_dir" not in f:
                    continue
                pages = f.get("pages", 0)
                if pages <= 0:
                    continue
                png_dirs.append({
                    "name": Path(f["png_dir"]).name,
                    "path": f"{folder_path.name}/{f['png_dir']}",
                    "page_count": pages,
                })
        except Exception:
            # Fallback: прямое сканирование для архивов без _meta.json
            for png_dir in folder_path.rglob('*'):
                if not png_dir.is_dir() or not png_dir.name.endswith('-png'):
                    continue
                png_files = list(png_dir.glob('*.png'))
                if not png_files:
                    continue
                try:
                    relative_path = png_dir.relative_to(cache_dir)
                    png_dirs.append({
                        'name': png_dir.name,
                        'path': str(relative_path.as_posix()),
                        'page_count': len(png_files),
                    })
                except ValueError:
                    continue

    png_dirs.sort(key=lambda x: x['path'])
    return png_dirs


def _list_png_files(dir_path: Path) -> list[dict]:
    """
    Возвращает список PNG файлов в директории.

    dir_path ожидается уже resolved (абсолютный), поэтому база для relative_to
    тоже должна быть resolved — иначе Path.relative_to бросит ValueError.

    Args:
        dir_path: абсолютный (resolved) путь к директории

    Returns:
        Список словарей с информацией о файлах:
        [{"name": "document_0001.png", "url": "/cache/...", "size": 123456}, ...]
    """
    if not dir_path.exists() or not dir_path.is_dir():
        return []

    cache_resolved = Path(CACHE_DIRECTORY).resolve()
    png_files = []

    for png_file in dir_path.glob('*.png'):
        if not png_file.is_file():
            continue

        # Формируем URL относительно cache mount point
        relative_path = png_file.relative_to(cache_resolved)
        url = f"{CACHE_URL_PATH}/{relative_path.as_posix()}"

        try:
            size = png_file.stat().st_size
        except Exception:
            size = 0

        png_files.append({
            'name': png_file.name,
            'url': url,
            'size': size
        })

    # Сортируем по имени (важно для правильного порядка страниц)
    png_files.sort(key=lambda x: x['name'])

    return png_files


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("/directories")
async def get_directories(request: Request):
    """
    API: получить список PNG директорий в data.cache.
    
    Returns:
        {"directories": [{"name": "...", "path": "...", "page_count": N}, ...]}
    """
    try:
        directories = _scan_png_directories()
        
        app_logger.debug(f"PNG directories scan: found {len(directories)} directories")
        
        return JSONResponse({
            'directories': directories,
            'total': len(directories)
        })
        
    except Exception as e:
        app_logger.error(f"Error scanning PNG directories: {e}", exc_info=True)
        return JSONResponse({'error': 'Internal server error'}, status_code=500)


@router.get("/{dir_path:path}/pages")
async def get_pages(
    dir_path: str,
    request: Request,
    want: Optional[int] = None,
):
    """
    API: получить список PNG страниц в директории.

    Разрешает вложенные пути (>= 2 сегментов).
    Последний сегмент должен быть PNG-директорией (заканчивается на -png).
    Boundary проверка — через канонический validate_and_resolve_under_base() (§3).

    Запрос — heartbeat просмотра: png-viewer опрашивает /pages, пока сервер рисует для want
    (rendering в ответе), и шлёт один запрос при смене страницы. Конвертер рендерит want, её соседей
    с обеих сторон, остаток окна вперёд, затем страницы позади want; частичная конвертация на паузе
    возобновляется сразу, до ответа, когда впереди от want готово меньше половины окна или позади want
    есть дырки (needs_resume, единственный триггер докрутки). Страницы want нет на диске частичной
    директории — long-poll: ответ, как только файл появился, но не позже PNG_PAGES_WAIT_SECONDS.

    Args:
        dir_path: путь к директории (например: "12345-ABC/dir1/report-png")
        want: страница (1-based), которую показывает вьюер

    Returns:
        {"pages": [{"name": "...", "url": "...", "size": N}, ...], "total": N,
         "directory": "...", "rendering": bool,
         "pages_total": N}   # pages_total — если известен из pre-scan
    """
    try:
        client_ip = request.client.host if request.client else "unknown"
        endpoint = f"/api/png/{dir_path}/pages"

        # Бизнес-правило 1: минимум 2 сегмента
        parts = dir_path.split('/')
        if len(parts) < 2:
            return JSONResponse(
                {'error': 'Invalid directory path format (need at least 2 segments)'},
                status_code=400,
            )

        # Бизнес-правило 2: последний сегмент — PNG-директория
        if not parts[-1].endswith('-png'):
            return JSONResponse(
                {'error': 'Last segment must be PNG directory (ending with -png)'},
                status_code=400,
            )

        # Безопасность: canonical boundary-check через Path.relative_to (§3).
        # dir_path берётся из URL-параметра, поэтому URL_DECODE_MAX_ROUNDS применяется.
        try:
            full_path = validate_and_resolve_under_base(
                Path(CACHE_DIRECTORY),
                dir_path,
                require_basename=False,
                client_ip=client_ip,
                endpoint=endpoint,
            )
        except PathValidationError:
            return JSONResponse({'error': 'Invalid path'}, status_code=400)

        # Проверяем существование
        if not full_path.exists():
            return JSONResponse({'error': 'Directory not found'}, status_code=404)

        if not full_path.is_dir():
            return JSONResponse({'error': 'Not a directory'}, status_code=400)

        # Имя архива и путь PNG-директории — из проверенного full_path, а не из сырых сегментов URL
        cache_root = Path(CACHE_DIRECTORY).resolve()
        rel_parts = full_path.relative_to(cache_root).parts
        archive_name = rel_parts[0]
        png_dir_rel = "/".join(rel_parts[1:])
        pdf_stem = full_path.name.removesuffix('-png')

        # Heartbeat просмотра: конвертер PDF работает, пока директорию смотрят; заодно LRU-метка архива
        touch_watch(full_path, want if want is not None and want >= 1 else None, cache_root / archive_name)
        partial = is_partial(full_path)
        # При идущей подготовке meta ещё может не быть — рисует она, а не докрутка.
        # resumable — докрутка вообще может стартовать (meta, partial, исходник на месте)
        preparing = is_preparing(archive_name)
        resumable = partial and not preparing and resumable_pdf_entry(archive_name, png_dir_rel) is not None
        if resumable:
            # Докрутка стартует сейчас, а не после ответа: иначе рендер want начался бы на тик позже,
            # а long-poll ниже ждал бы страницу, которую никто не рисует. sleep(0) отдаёт задаче
            # управление до ожидания — она успевает решить, докручивать ли, и записать _prepare.json
            task = asyncio.create_task(resume_pdf_conversion(archive_name, png_dir_rel))
            _resume_tasks.add(task)
            task.add_done_callback(_resume_tasks.discard)
            await asyncio.sleep(0)

        # Маркер общего числа страниц (записывается pre-scan'ом)
        pages_total = read_pages_total(full_path)

        # Читатель ждёт: вьюер показывает «Страница N подготавливается…». Long-poll: отвечаем, как
        # только PNG want появился, — читатель видит страницу в момент рендера, а не на следующем
        # опросе. Ждать есть смысл, только если страницу кто-то рисует (подготовка или докрутка, которая
        # может стартовать) — иначе 1,5 с держались бы ради PNG, которого не будет.
        # converting — PDF архива, который рендерится к началу ожидания: этот же — ждём
        # рендер, другой — конкуренция PDF одного архива, пусто — докрутка не идёт.
        # _prepare.json читается, только пока страницы нет
        if (preparing or resumable) and partial and pages_total and want is not None and 1 <= want <= pages_total:
            want_png = full_path / generate_png_filename(pdf_stem, want - 1)
            if not want_png.exists():
                converting = read_prepare_status(archive_name).get("converting_path", "")
                started = time.perf_counter()
                deadline = started + PNG_PAGES_WAIT_SECONDS
                ready = False
                while (left := deadline - time.perf_counter()) > 0:
                    await asyncio.sleep(min(PNG_PAGES_WAIT_STEP_SECONDS, left))
                    if want_png.exists():
                        ready = True
                        break
                waited_ms = round((time.perf_counter() - started) * 1000)
                # Листинг ниже собирается после ожидания — появившаяся страница попадёт в ответ
                log_with_data(logging.INFO, "PDF page wait", archive=archive_name, png_dir=png_dir_rel,
                              want=want, done=count_pngs(full_path), total=pages_total,
                              converting=converting, waited_ms=waited_ms, ready=ready)

        # Получаем список файлов (full_path resolved → _list_png_files использует resolved базу)
        pages = _list_png_files(full_path)

        app_logger.debug(f"PNG pages listing: {dir_path} - {len(pages)} pages")

        # Сервер рисует для want: идёт конвертация архива или нужна докрутка — тот же гистерезис, что
        # у resume_pdf_conversion. Иначе вьюер замолкает до смены страницы, и забытая вкладка не шлёт
        # heartbeat раз в 2 с всю ночь. Heartbeat этого запроса уже записан — правило считается для его want
        rendering = (pages_total is not None and len(pages) < pages_total
                     and (preparing or (resumable and needs_resume(full_path, pdf_stem, pages_total))))

        response_data = {
            'pages': pages,
            'total': len(pages),
            'directory': dir_path,
            'rendering': rendering,
        }
        if pages_total is not None:
            response_data['pages_total'] = pages_total

        return JSONResponse(response_data)

    except Exception as e:
        app_logger.error(f"Error listing PNG pages: {e}", exc_info=True)
        return JSONResponse({'error': 'Internal server error'}, status_code=500)
