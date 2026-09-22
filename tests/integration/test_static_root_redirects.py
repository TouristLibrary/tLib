# Version 1.0 - 22.09.2026 11:10:00 GMT
# Integration tests: routers/static_router.py::root
# Описание: Проверяет редиректы GET / без рендера страницы.
#           Порядок: legacy ?id= раньше очистки меток — один 301 сразу на адрес отчёта.
#           Хвостовой «=» у компактного шифра тоже уходит 301 на чистый адрес.
#           Приложение собирается здесь, без общего conftest: тот заточен под auth и upload.

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
