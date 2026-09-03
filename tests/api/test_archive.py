"""
API tests for archive_router happy path.

Тесты /api/archive/{filename}/contents и /api/archive/{filename}/all-tracks.
Имя архива в URL передаётся БЕЗ расширения .zip — endpoint сам добавляет .zip
при поиске файла в data/ (archive_router.py).

Используется посевной отчёт 00001-TST (Шифр=1, ДопШифр=TST) — синтетическая
zip-фикстура, гарантированно присутствующая на правильно засеянном стенде.
archive_router работает только с .zip (allowed_suffixes=[".zip"]), поэтому
pdf-отчёты этим эндпоинтом не покрываются.

Если посевной отчёт недоступен — happy-path тесты пропускаются с явным
предупреждением (см. conftest.py: sample_archive_id).

Запуск: BASE_URL=https://your-server.example.com python -m pytest api/test_archive.py -v
"""
from __future__ import annotations

import os

import pytest
import httpx


# Символы блочной графики CP437 (признак кракозябр при неверном декодировании)
_BOX_RANGE = range(0x2500, 0x2580)


def _has_mojibake(text: str) -> bool:
    return any(ord(c) in _BOX_RANGE for c in text)


@pytest.fixture(scope="session")
def archive_client() -> httpx.Client:
    """
    Отдельный httpx.Client с увеличенным таймаутом для запросов к archive-endpoint
    (чтение оглавления ZIP на сервере может быть медленнее обычного API).
    Не загрязняет пул соединений общего session-клиента.
    """
    base = os.environ.get("BASE_URL", "").rstrip("/")
    with httpx.Client(base_url=base, timeout=120.0, follow_redirects=True) as c:
        yield c


@pytest.fixture(scope="session")
def archive_name(sample_archive_id: str) -> str:
    """
    Возвращает нормализованное имя посевного архива (00001-TST).
    Зависит от sample_archive_id — если сид недоступен, тест будет пропущен
    с явной причиной ещё на уровне session-фикстуры в conftest.py.
    """
    return sample_archive_id


# ---------------------------------------------------------------------------
# /contents — happy path
# ---------------------------------------------------------------------------


def test_contents_returns_200_with_files(archive_client: httpx.Client, archive_name: str) -> None:
    resp = archive_client.get(f"/api/archive/{archive_name}/contents")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Ответ: {"name": ..., "files": [...], "download_url": ...}
    assert "files" in body, f"Нет поля 'files' в ответе: {list(body.keys())}"
    files = body["files"]
    assert len(files) > 0, "Список файлов пустой"


def test_contents_filenames_have_no_mojibake(archive_client: httpx.Client, archive_name: str) -> None:
    resp = archive_client.get(f"/api/archive/{archive_name}/contents")
    assert resp.status_code == 200
    files = resp.json().get("files", [])
    for item in files:
        name = item["name"]
        assert not _has_mojibake(name), f"Кракозябры в имени файла: {name!r}"


def test_contents_no_macos_metadata_files(archive_client: httpx.Client, archive_name: str) -> None:
    resp = archive_client.get(f"/api/archive/{archive_name}/contents")
    assert resp.status_code == 200
    files = resp.json().get("files", [])
    for item in files:
        name = item["name"]
        assert "__MACOSX/" not in name, f"Метафайл macOS в списке: {name!r}"
        assert not name.startswith("._"), f"Dot-underscore файл в списке: {name!r}"


# ---------------------------------------------------------------------------
# /contents — негатив (быстрый запрос — используем обычный client)
# ---------------------------------------------------------------------------


def test_contents_missing_archive_returns_404(client: httpx.Client) -> None:
    # Имя без .zip — 99999-NOEXIST ищет несуществующий 99999-NOEXIST.zip
    resp = client.get("/api/archive/99999-NOEXIST/contents")
    assert resp.status_code == 404, f"Ожидали 404, получили {resp.status_code}"


# ---------------------------------------------------------------------------
# /all-tracks
# ---------------------------------------------------------------------------


def test_all_tracks_does_not_return_500(archive_client: httpx.Client, archive_name: str) -> None:
    """all-tracks возвращает 200 (zip с треками) или 404 (треков нет) — главное не 500."""
    resp = archive_client.get(f"/api/archive/{archive_name}/all-tracks")
    assert resp.status_code in (200, 404), (
        f"Ожидали 200 или 404, получили {resp.status_code}: {resp.text[:200]}"
    )
