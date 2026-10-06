# Version 1.3 - 06.10.2026 12:24:38 GMT
# Integration tests: routers/static_router.py — редиректы корня и путей старого сайта
# Описание: Проверяет редиректы GET / и путей файлов старого сайта без рендера страницы.
#           Порядок: legacy ?id= раньше очистки меток — один 301 сразу на адрес отчёта.
#           Хвостовой «=» у компактного шифра тоже уходит 301 на чистый адрес.
#           HEAD / и HEAD /index.html обслуживаются теми же обработчиками, что и GET.
#           Приложение собирается здесь, без общего conftest: тот заточен под auth и upload.
#           1.1: HEAD / → 200, HEAD /index.html → 301 на /.
#           1.2: пути файлов старого сайта (/png|pdf|tif|zip/aa/bb/<id>.*, /files/<id>/...)
#                → 301 на корень отчёта без страницы; промах → /?notfound=1; мусор → 404.
#                Регресс: /doc.aspx?id=&page= по-прежнему переносит страницу.
#           1.3: шапка описывает весь охват файла, не только корень.

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from config import REDIRECT_LEGACY_FILE_KINDS
from routers.static_router import router


@pytest.fixture()
def root_client() -> TestClient:
    """Корень с таблицей редиректов, без следования по 301."""
    application = FastAPI()
    application.include_router(router)
    application.state.redirect_table = {"id=28466": "3725"}
    with TestClient(application, follow_redirects=False) as client:
        yield client


def test_legacy_id_with_utm_redirects_once_to_report(root_client: TestClient):
    """?id= с меткой не заходит на промежуточный /?id= без метки."""
    response = root_client.get("/?id=28466&utm_source=x")
    assert response.status_code == 301
    assert response.headers["location"] == "/?3725"


def test_trailing_equals_redirects_to_clean_cipher(root_client: TestClient):
    response = root_client.get("/?3725=")
    assert response.status_code == 301
    assert response.headers["location"] == "/?3725"


def test_head_root_returns_200(root_client: TestClient):
    """HEAD / обслуживается тем же обработчиком, что и GET (не 405)."""
    response = root_client.head("/")
    assert response.status_code == 200


def test_head_index_html_redirects_to_root(root_client: TestClient):
    response = root_client.head("/index.html")
    assert response.status_code == 301
    assert response.headers["location"] == "/"


@pytest.mark.parametrize("kind", REDIRECT_LEGACY_FILE_KINDS)
def test_legacy_file_path_redirects_to_report(root_client: TestClient, kind: str):
    """Ведущие нули id сняты, номер страницы из имени не переносится."""
    response = root_client.get(f"/{kind}/02/84/028466.58.{kind}")
    assert response.status_code == 301
    assert response.headers["location"] == "/?3725"


def test_legacy_file_path_without_page_redirects_to_report(root_client: TestClient):
    response = root_client.get("/pdf/02/84/028466.pdf")
    assert response.status_code == 301
    assert response.headers["location"] == "/?3725"


def test_legacy_files_attachment_redirects_to_report(root_client: TestClient):
    response = root_client.get("/files/28466/0/report.pdf")
    assert response.status_code == 301
    assert response.headers["location"] == "/?3725"


def test_legacy_file_path_unknown_id_redirects_to_notfound(root_client: TestClient):
    """Промах — тот же ответ, что у /doc.aspx с неизвестным id."""
    response = root_client.get("/png/00/00/000001.png")
    assert response.status_code == 302
    assert response.headers["location"] == "/?notfound=1"


@pytest.mark.parametrize("path", [
    "/png/02/84/readme.png",       # имя без id
    "/docx/02/84/028466.docx",     # не каталог хранилища старого сайта
    "/files/abc/0/report.pdf",     # id вложения не число
])
def test_legacy_file_path_garbage_returns_404(root_client: TestClient, path: str):
    """Мусор под старыми префиксами не порождает редиректов."""
    assert root_client.get(path).status_code == 404


def test_head_legacy_file_path_redirects(root_client: TestClient):
    response = root_client.head("/png/02/84/028466.58.png")
    assert response.status_code == 301
    assert response.headers["location"] == "/?3725"


def test_doc_aspx_keeps_page(root_client: TestClient):
    """Регресс: query-формат после выноса ядра по-прежнему открывает страницу PDF."""
    response = root_client.get("/doc.aspx?id=28466&page=3")
    assert response.status_code == 301
    assert response.headers["location"] == "/?3725#tab=pdf&p=3"
