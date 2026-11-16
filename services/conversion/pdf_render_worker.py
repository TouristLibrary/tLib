# PDF Render Worker для TlibWebApp
# Описание: Функции процесса-воркера (ProcessPoolExecutor из pdf_to_png_service): рендер PDF→PNG
#           и оптимизация картинок архива. PyMuPDF держит GIL весь get_pixmap (~0,5–1,3 с на страницу,
#           столько же на крупное фото): в потоке главного процесса поиск и /pages ждут конца вызова.
#           В отдельном процессе GIL свой.
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

import functools
import os
import shutil
import signal

import fitz  # PyMuPDF


def _picklable_errors(fn):
    """
    Любую ошибку задачи отдаёт родителю как RuntimeError с типом и текстом исходной.

    Исключения самого MuPDF (FzError*: «malloc failed» на странице огромного размера,
    «unknown image file format») держат SWIG-объект и не проходят pickle: родитель получил бы
    «cannot pickle 'SwigPyObject' object» вместо причины. Исходная трассировка уходит в лог
    родителя как причина (from e): её текст concurrent.futures передаёт отдельно от исключения.
    """
    @functools.wraps(fn)
    def wrapper(*args):
        try:
            return fn(*args)
        except Exception as e:
            raise RuntimeError(f"{type(e).__name__}: {e}") from e
    return wrapper


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


@_picklable_errors
def page_count(pdf_path: str) -> int:
    """Число страниц PDF."""
    doc = fitz.open(pdf_path)
    try:
        return len(doc)
    finally:
        doc.close()


@_picklable_errors
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


@_picklable_errors
def optimize_image(source_path: str, output_path: str,
                   max_width: int, max_height: int,
                   jpeg_quality: int, as_png: bool) -> tuple[int, int, int, int, bool]:
    """
    Уменьшает растр до max_width×max_height либо копирует его без перекодирования.

    В этом процессе, а не в потоке uvicorn: Pixmap и get_pixmap держат GIL, и поиск,
    идущий в то же время, растягивался бы в сотни раз.
    Каталог output_path создаёт родитель. Размеры — в пикселях, не в PDF-points.

    Returns:
        (src_w, src_h, dst_w, dst_h, copied). copied — файл скопирован байт в байт.
    """
    pix = fitz.Pixmap(source_path)
    src_w, src_h = pix.width, pix.height
    pix = None  # освобождаем память до копирования или ресайза

    if src_w <= max_width and src_h <= max_height:
        shutil.copyfile(source_path, output_path)
        return src_w, src_h, src_w, src_h, True

    # Масштаб по пикселям; matrix — через rect страницы, он в points
    scale = min(max_width / src_w, max_height / src_h)
    dst_w = max(1, round(src_w * scale))
    dst_h = max(1, round(src_h * scale))

    with fitz.open(source_path) as doc:
        page = doc[0]
        m = dst_w / page.rect.width
        out = page.get_pixmap(matrix=fitz.Matrix(m, m), alpha=False)
        if as_png:
            out.save(output_path, "png")
        else:
            out.save(output_path, "jpeg", jpg_quality=jpeg_quality)

    return src_w, src_h, dst_w, dst_h, False
