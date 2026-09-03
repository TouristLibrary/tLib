# Version 1.3 - 29.07.2026 19:00:00 GMT
# Config Router для TlibWebApp
# Описание: API endpoint для получения конфигурации сервера клиентом.
#           GET /api/config возвращает критичные настройки из config/,
#           которые должны быть синхронизированы между сервером и клиентом:
#           - paths.localArchive (LOCAL_ARCHIVE_PATH)
#           - paths.pcloudData (PCLOUD_DATA_BASE_URL) — базовый URL зеркала в pCloud
#           - extensions.gpsTracks (GPS_TRACK_EXTENSIONS)
#           - extensions.images (INLINE_EXTENSIONS, только изображения)
#           - specialValues.noDopShifr (значение "нет" для ДопШифр)
#           - mimeTypes (MIME_TYPES, ключи без точек)
#           Устраняет дублирование конфигурации между Python и JavaScript.
# 1.3: добавлен paths.pcloudData для cloud.html.

from fastapi import APIRouter

# Импорт конфигурации
import config
from config import REFERENCE_FIELDS, PCLOUD_DATA_BASE_URL

# Создаем роутер
router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config", 
            summary="Получить конфигурацию сервера",
            description="Возвращает настройки сервера для синхронизации с клиентом")
async def get_client_config():
    """
    Возвращает конфигурацию сервера для клиента.
    
    Включает:
    - Пути к ресурсам (архивы)
    - Расширения файлов (GPS треки, изображения)
    - Специальные значения (значение "нет" для ДопШифр)
    - MIME типы файлов (без точек в ключах)
    
    Returns:
        dict: Конфигурация с флагом success и данными
    """
    try:
        # Получаем значение "нет" для ДопШифр из конфигурации полей
        # default_prefix = ["", "нет"], берем второй элемент
        no_dop_shifr = REFERENCE_FIELDS['dopshifr']['default_prefix'][1]
        
        # Формируем список расширений GPS треков без точек и сортируем
        gps_extensions = sorted(ext.lstrip('.') for ext in config.GPS_TRACK_EXTENSIONS)
        
        # Формируем список расширений изображений (используем атомарную категорию из config)
        image_extensions = sorted(ext.lstrip('.') for ext in config.ALL_IMAGE_EXTENSIONS)
        
        # Формируем MIME типы без точек в ключах для клиента
        mime_types = {ext.lstrip('.'): mime for ext, mime in config.MIME_TYPES.items()}
        
        return {
            "success": True,
            "data": {
                "paths": {
                    "localArchive": config.LOCAL_ARCHIVE_PATH,
                    "pdfApi": "/api/pdf",
                    "pcloudData": PCLOUD_DATA_BASE_URL
                },
                "extensions": {
                    "gpsTracks": gps_extensions,
                    "images": image_extensions
                },
                "specialValues": {
                    "noDopShifr": no_dop_shifr
                },
                "mimeTypes": mime_types
            }
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Ошибка получения конфигурации: {str(e)}"
        }
