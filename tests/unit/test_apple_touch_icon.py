# Version 1.0 - 06.10.2026 12:45:04 GMT
# Unit tests: assets/apple-touch-icon.png и ссылки на него в HTML-страницах
# Описание: Safari на iPhone без <link rel="apple-touch-icon"> запрашивает /apple-touch-icon.png
#           в корне сайта и получает 404. Проверяет, что иконка есть, это PNG 180×180,
#           и что каждая страница с favicon ссылается на неё.

from __future__ import annotations

import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ICON = ROOT / "assets" / "apple-touch-icon.png"
PAGES = ["index.html", "about.html", "cloud.html", "oldscan.html", "png-viewer.html"]
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def test_icon_is_png_180():
    # Размер из чанка IHDR: Pillow не входит в зависимости проекта
    head = ICON.read_bytes()[:24]
    assert head[:8] == PNG_SIGNATURE
    assert head[12:16] == b"IHDR"
    assert struct.unpack(">II", head[16:24]) == (180, 180)


@pytest.mark.parametrize("page", PAGES)
def test_page_links_apple_touch_icon(page: str):
    html = (ROOT / page).read_text(encoding="utf-8")
    assert '<link rel="apple-touch-icon" sizes="180x180" href="assets/apple-touch-icon.png">' in html
