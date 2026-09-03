# Version 1.0 - 24.11.2025 17:54:36 GMT
# Middleware для фильтрации HTTP методов
# Описание: Блокирует потенциально опасные HTTP методы для повышения безопасности. Разрешает только безопасные методы:
#           GET, HEAD, OPTIONS для всех запросов, POST только для API endpoints (/api/*).
#           Блокирует PUT, DELETE, PATCH и другие методы модификации, возвращает HTTP 405 Method Not Allowed.
#           Логирует попытки использования запрещенных методов через security_logger.

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from logging_config import security_logger
from config import ALLOWED_HTTP_METHODS, API_PATH_PREFIX


class HTTPMethodFilterMiddleware(BaseHTTPMiddleware):
    """
    Middleware для фильтрации HTTP методов.
    Разрешает только безопасные HTTP методы (GET, HEAD, OPTIONS, POST для API).
    """
    
    async def dispatch(self, request: Request, call_next):
        """
        Проверяет HTTP метод запроса и блокирует небезопасные методы
        
        Args:
            request: HTTP запрос
            call_next: Следующий обработчик в цепочке
            
        Returns:
            Response: HTTP ответ (либо от следующего обработчика, либо 405 для запрещенных методов)
        """
        # Разрешенные методы: GET для статики и чтения, POST только для API поиска
        allowed_methods = ALLOWED_HTTP_METHODS
        
        # POST разрешен только для API endpoints
        if request.method == "POST" and not request.url.path.startswith(API_PATH_PREFIX):
            security_logger.log_invalid_request(
                request.client.host,
                request.url.path,
                f"POST method not allowed for non-API paths"
            )
            return JSONResponse(
                {"error": "Method not allowed"},
                status_code=405,
                headers={"Allow": "GET, HEAD, OPTIONS"}
            )
        
        if request.method not in allowed_methods:
            security_logger.log_invalid_request(
                request.client.host,
                request.url.path,
                f"Method {request.method} not allowed"
            )
            return JSONResponse(
                {"error": "Method not allowed"},
                status_code=405,
                headers={"Allow": "GET, HEAD, OPTIONS, POST"}
            )
        
        # Метод разрешен - продолжаем обработку
        response = await call_next(request)
        return response
