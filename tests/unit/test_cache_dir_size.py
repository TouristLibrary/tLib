# Version 1.0 - 26.09.2026 11:49:27 GMT
# Тесты подсчёта cache_size_bytes (services/cache/cache_pipeline._compute_cache_dir_size)
# Описание: Размер папки кеша считается под lock, но должен совпадать с тем, что останется
#           на диске после его снятия: _work/ (перераспакованный PDF докрутки), lockdir
#           вместе с info.txt, _prepare.json и tmp-файлы атомарной записи не учитываются.

from services.cache.cache_pipeline import _compute_cache_dir_size


def _write(path, size):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return size


def test_counts_only_what_survives_lock_release(tmp_path):
    cache_dir = tmp_path / "00001-TST"

    # Временное на время запуска — удаляется в finally подготовки/докрутки
    _write(cache_dir / "_work" / "x.pdf", 10_000)
    _write(cache_dir / "_prepare.json", 300)
    _write(cache_dir / "_prepare.lockdir" / "info.txt", 70)
    _write(cache_dir / ".tmp-1", 500)

    # Остаётся в кеше
    kept = (
        _write(cache_dir / "a-png" / "a_0001.png", 4_000)
        + _write(cache_dir / "a-png" / "_watch.json", 40)
        + _write(cache_dir / "_meta.json", 900)
    )

    assert _compute_cache_dir_size(cache_dir) == kept
