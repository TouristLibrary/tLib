# Version 1.0 - 03.10.2026 09:36:10 GMT
# PDF Render Worker для TlibWebApp
# Описание: Функции процесса-воркера рендера PDF→PNG (ProcessPoolExecutor из pdf_to_png_service).
#           PyMuPDF держит GIL весь get_pixmap/tobytes (~0,5–1,3 с на страницу): рендер в потоке
#           главного процесса морит цикл событий, и запрос, которому нужны десятки проходов
#           цикла (поиск, /pages, PNG), ждёт конца окна рендера. В отдельном процессе GIL свой.
#
#           Модуль — лист: импортирует только stdlib и fitz. Никаких config/logging_config:
#           logging_config при импорте открывает RotatingFileHandler, и второй процесс писал бы
#           и ротировал те же app.log/critical.log. Поэтому __init__ пакетов services и
#           services/conversion должны оставаться без импортов, а аргументы функций — примитивы
#           (str/int/float/bool): pickle между процессами без сюрпризов.
#
#           Пул создаётся с контекстом spawn, а не fork: fork из многопоточного uvicorn копирует
#           блокировки, захваченные другими потоками в момент fork (логгер, аллокатор), и ребёнок
#           может зависнуть на них. Spawn-ребёнок стартует чистым интерпретатором и импортирует
#           только этот модуль; __main__ родителя он не переимпортирует, пока сервер запущен как
#           python -m uvicorn (имя uvicorn.__main__ spawn пропускает). При запуске python app.py
#           ребёнок выполнил бы app.py как __mp_main__ — со всеми импортами и логгерами.

import os
import signal

import fitz  # PyMuPDF


def ignore_stop_signals() -> None:
    """
    Initializer воркера: SIGTERM и SIGINT обрабатывает только родитель.

    systemd при остановке сервиса шлёт SIGTERM всем процессам cgroup, Ctrl+C в терминале —
    SIGINT всей группе. Воркер, убитый сигналом, ломает пул (BrokenProcessPool), и запуск,
    который дорисовывал окно, записал бы error в _meta.json и закрыл директорию на готовых
    страницах. Uvicorn же дожидается фоновых задач запросов до lifespan shutdown: воркер
    дорисовывает окно и выходит, когда родитель останавливает пул; залипшую остановку
    добивает SIGKILL по TimeoutStopSec.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)


def page_count(pdf_path: str) -> int:
    """Число страниц PDF (исключение MuPDF уходит родителю через future)."""
    doc = fitz.open(pdf_path)
    try:
        return len(doc)
    finally:
        doc.close()


def render_page(pdf_path: str, page_index: int, out_path: str,
                zoom: float, colorspace: str, alpha: bool) -> None:
    """
    Рендерит одну страницу PDF в PNG out_path.

    PDF открывается на каждую страницу: 2–15 мс против ~1 с рендера, зато воркер не держит
    открытых документов между запусками и страницы разных PDF чередуются без состояния.
    PNG пишется атомарно (tmp + os.replace): готовая страница больше не перерисовывается,
    поэтому обрыв записи не должен оставлять битый файл. tmp-имя не матчится glob("*.png")
    и исключается из cache_size_bytes ('.tmp-').
    """
    cs = fitz.csGRAY if colorspace == "gray" else fitz.csRGB
    matrix = fitz.Matrix(zoom, zoom)

    doc = fitz.open(pdf_path)
    try:
        pixmap = doc[page_index].get_pixmap(matrix=matrix, colorspace=cs, alpha=alpha)
        png_data = pixmap.tobytes(output="png")
        pixmap = None  # освобождаем память до записи
    finally:
        doc.close()

    tmp_path = f"{out_path}.tmp-{os.getpid()}"
    with open(tmp_path, "wb") as f:
        f.write(png_data)
    os.replace(tmp_path, out_path)
