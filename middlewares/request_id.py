# Version 1.0 - 30.12.2025 07:48:27 GMT
# Request ID Middleware для TlibWebApp
# Описание: Middleware для добавления уникального request_id к каждому HTTP запросу.
#           Генерирует UUID для каждого запроса и сохраняет его в контексте через request_id_var (ContextVar).
#           Добавляет заголовок X-Request-ID в ответы для трассировки запросов.
#           Позволяет связать все логи одного запроса через единый идентификатор.
#           Используется совместно с unified logging system (logging_config.py).

import uuid
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from logging_config import app_logger, request_id_var


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware для добавления уникального request_id к каждому HTTP запросу.
    Это позволяет связать все логи одного запроса через единый идентификатор.
    """
    
    async def dispatch(self, request: Request, call_next):
        """
        Обрабатывает запрос и добавляет уникальный request_id
        
        Args:
            request: HTTP запрос
            call_next: Следующий обработчик в цепочке
            
        Returns:
            Response: HTTP ответ с добавленным заголовком X-Request-ID
        """
        # Генерируем уникальный request_id
        req_id = str(uuid.uuid4())
        request_id_var.set(req_id)
        
        app_logger.debug(f"Новый запрос: {request.method} {request.url.path}")
        
        # Получаем ответ от следующего обработчика
        response = await call_next(request)
        
        # Добавляем в заголовки ответа для трассировки
        response.headers["X-Request-ID"] = req_id
        
        return response
