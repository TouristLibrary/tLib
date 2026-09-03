# Version 2.3 - 07.01.2026 13:33:25 GMT
# Lists Router для TlibWebApp
# Описание: API endpoints для получения справочных списков из базы данных. Предоставляет шесть endpoint'ов:
#           GET /api/dopshifr-list, /api/raion-obshiy-list, /api/tip-list, /api/kategoria-s-list, /api/kategoria-po-list,
#           /api/reports-count, /api/reference-version. Возвращает уникальные значения полей и общее количество отчетов из БД.
#           Оба endpoint'а категорий (kategoria-s-list и kategoria-po-list) возвращают единый объединённый список.
#           Данные кэшируются при запуске приложения в app.state. reference-version позволяет фронтенду проверять актуальность
#           справочников без загрузки самих списков. Использует функцию-фабрику для создания endpoint'ов.

from fastapi import APIRouter, Request, Response

# Импорт конфигурации
from config import CACHE_CONTROL_NO_STORE, STATE_REFERENCE_VERSION

# Импорт логгеров
from logging_config import app_logger

# Импорт конфигурации полей
from config import REFERENCE_FIELDS, STATE_KATEGORIA_UNIFIED, STATE_REPORTS_COUNT

# Создаем роутер
router = APIRouter(prefix="/api", tags=["lists"])


# ============================================================================
# ФАБРИКА ENDPOINT'ОВ
# ============================================================================

def create_list_endpoint(state_field: str, field_name: str):
    """
    Фабрика для создания endpoint'а справочного списка
    
    Создает асинхронную функцию-обработчик для получения данных
    из app.state с единообразной обработкой ошибок.
    
    Args:
        state_field: Имя поля в app.state (например, 'dopshifr_list')
        field_name: Отображаемое имя поля для логов и ошибок (например, 'ДопШифр')
        
    Returns:
        Асинхронная функция-обработчик endpoint'а
    """
    async def endpoint(request: Request):
        """
        Возвращает справочный список из app.state
        """
        try:
            # Получаем данные из app.state
            data = getattr(request.app.state, state_field)
            
            return {
                "success": True,
                "data": data
            }
        except Exception as e:
            app_logger.error(f"Ошибка получения списка {field_name}: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Ошибка получения списка {field_name}"
            }
    
    # Устанавливаем имя функции для корректного отображения в логах и документации
    endpoint.__name__ = f"get_{state_field}"
    return endpoint


# ============================================================================
# ДИНАМИЧЕСКАЯ РЕГИСТРАЦИЯ ENDPOINT'ОВ
# ============================================================================

# Регистрируем все endpoint'ы в цикле на основе конфигурации
for field_config in REFERENCE_FIELDS.values():
    api_endpoint = field_config['api_endpoint']
    state_key = field_config['state_key']
    display_name = field_config['display_name']
    
    # Для категорий используем unified список
    if field_config.get('use_unified'):
        state_key = STATE_KATEGORIA_UNIFIED
    
    router.add_api_route(
        f"/{api_endpoint}",
        create_list_endpoint(state_key, display_name),
        methods=["GET"],
        summary=f"Получить список {display_name}",
        description=f"Возвращает все уникальные значения поля {display_name} из базы данных"
    )

@router.get("/reference-version", summary="Получить версию справочников",
            description="Возвращает текущую версию справочных списков из app.state (меняется при обновлении БД)")
async def get_reference_version(request: Request, response: Response):
    """
    Возвращает версию справочников из app.state.

    Используется фронтендом для дешёвой проверки актуальности справочников.
    """
    try:
        response.headers["Cache-Control"] = CACHE_CONTROL_NO_STORE
        version = getattr(request.app.state, STATE_REFERENCE_VERSION, None)
        return {
            "success": True,
            "version": version
        }
    except Exception as e:
        app_logger.error(f"Ошибка получения reference_version: {e}", exc_info=True)
        return {
            "success": False,
            "error": "Ошибка получения версии справочников"
        }


# ============================================================================
# ENDPOINT ДЛЯ КОЛИЧЕСТВА ОТЧЕТОВ
# ============================================================================

@router.get("/reports-count", summary="Получить количество отчетов", 
            description="Возвращает общее количество отчетов в базе данных (из кэша)")
async def get_reports_count(request: Request):
    """
    Возвращает общее количество отчетов в БД
    
    Данные загружаются из app.state (кэшируются при старте сервера).
    """
    try:
        count = getattr(request.app.state, STATE_REPORTS_COUNT, 0)
        return {
            "success": True,
            "count": count
        }
    except Exception as e:
        app_logger.error(f"Ошибка получения количества отчетов: {e}", exc_info=True)
        return {
            "success": False,
            "error": "Ошибка получения количества отчетов"
        }

