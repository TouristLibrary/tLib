# Version 1.0 - 17.04.2026 00:00:00 GMT
# File Watcher Stability - Групповой stability-window
#
# Назначение: отслеживать стабильность файлов в data.up/20_go/ между тиками сканирования.
# Файл считается стабильным, если его size и mtime не менялись N последовательных сканов подряд.
# Группа стабильна, только если ВСЕ входящие в неё файлы стабильны.
#
# Решаемая проблема: File Watcher ранее подхватывал ZIP-архивы в середине GoogleDrive-синхронизации,
# что приводило к zipfile.BadZipFile (truncated EOCD). Теперь группа не попадает в пайплайн,
# пока все её файлы не «замрут» на N тиков.
#
# Публичный API:
#   observe(paths)           — вызвать один раз в начале каждого тика для всех видимых файлов
#   prune(existing)          — удалить из state ключи, которых уже нет в data.up/20_go/
#   is_stable(path, n)       — True если файл стабилен >= n сканов подряд
#   is_group_stable(files, n) — True если все файлы группы стабильны

from pathlib import Path
from typing import Iterable

# Хранит последнее наблюдение для каждого файла:
# Path -> (size_bytes, mtime_float, consecutive_stable_count)
_observations: dict[Path, tuple[int, float, int]] = {}


def observe(paths: Iterable[Path]) -> None:
    """
    Обновляет счётчики стабильности для переданных файлов.

    Для каждого файла:
    - Читает текущие size и mtime.
    - Если (size, mtime) совпадают с прошлым наблюдением — увеличивает счётчик.
    - Если изменились — сбрасывает счётчик в 1 (текущий скан = первое наблюдение).

    Вызывать один раз в начале каждого тика, до вызова is_group_stable.
    """
    for path in paths:
        try:
            st = path.stat()
            size = st.st_size
            mtime = st.st_mtime
        except OSError:
            # Файл исчез между scan_new_files и observe — пропускаем
            continue

        prev = _observations.get(path)
        if prev is not None and prev[0] == size and prev[1] == mtime:
            _observations[path] = (size, mtime, prev[2] + 1)
        else:
            _observations[path] = (size, mtime, 1)


def prune(existing: set[Path]) -> None:
    """
    Удаляет из state ключи, которых нет в переданном множестве existing.

    Вызывать после observe(), передавая актуальный set файлов из data.up/20_go/.
    Это обеспечивает:
    - Очистку записей файлов, ушедших в processing/ (иначе при перезаливе
      тот же путь выглядел бы «уже стабильным»).
    - Очистку «брошенных» файлов, удалённых вручную.
    """
    stale = [p for p in _observations if p not in existing]
    for p in stale:
        del _observations[p]


def is_stable(path: Path, required: int) -> bool:
    """
    Возвращает True, если файл наблюдался не менее required раз подряд
    с одинаковыми size+mtime.

    Возвращает False, если путь ещё не в state (первый скан).
    """
    entry = _observations.get(path)
    if entry is None:
        return False
    return entry[2] >= required


def is_group_stable(files: list[Path], required: int) -> bool:
    """
    Возвращает True, только если ВСЕ файлы группы стабильны.

    Используется для задержки целой группы (JSON + ZIP), пока оба файла
    не «замрут» — это предотвращает расщепление полной группы на два цикла
    (json_only + partial) при одновременной заливке нескольких файлов.
    """
    return all(is_stable(f, required) for f in files)
