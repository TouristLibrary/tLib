# Version 2.1 - 25.12.2025 15:26:06 GMT
# Middleware для детекции Path Traversal атак
# Описание: Трехэтапная защита от Path Traversal атак:
#           1) Детектирует URL-encoded паттерны (%2e%2e) ДО декодирования
#           2) Детектирует декодированные паттерны (/../) ПОСЛЕ декодирования Uvicorn
#           3) Мониторит подозрительные 404/200 ответы (детектирует последствия нормализованных атак)
#           Упрощенная практичная версия - работает с реальными возможностями ASGI/Uvicorn.
#           Логирует все попытки атак и подозрительную активность в critical.log через security_logger.

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from logging_config import security_logger
from config import (
    SECURITY_WHITELIST_IPS, SECURITY_CLI_TOOLS,
    SECURITY_URL_ENCODED_PATTERNS, SECURITY_DECODED_PATTERNS, SECURITY_SUSPICIOUS_PATHS
)


class PathSecurityMiddleware(BaseHTTPMiddleware):
    """
    Middleware для детекции Path Traversal атак в URL.
    
    Использует трехэтапный подход:
    - Этап 1: Детекция URL-encoded паттернов (%2e%2e) до декодирования
    - Этап 2a: Детекция декодированных паттернов (/../) после декодирования
    - Этап 2b: Мониторинг подозрительных 404 (системные пути)
    - Этап 2c: Мониторинг подозрительных 404 к корню с CLI
    - Этап 2d: Мониторинг подозрительных 200 к корню с CLI (исключая localhost)
    
    Детектирует encoded, decoded и normalized атаки с полным покрытием.
    Исключает localhost для предотвращения ложных срабатываний от healthcheck.
    """
    
    # Паттерны безопасности импортируются из config.py
    URL_ENCODED_PATTERNS = SECURITY_URL_ENCODED_PATTERNS
    DECODED_PATTERNS = SECURITY_DECODED_PATTERNS
    SUSPICIOUS_404_PATHS = SECURITY_SUSPICIOUS_PATHS
    WHITELIST_IPS = SECURITY_WHITELIST_IPS
    
    async def dispatch(self, request: Request, call_next):
        """
        Трехэтапная детекция Path Traversal атак:
        1. ДО обработки: детектируем URL-encoded паттерны
        2a. ПОСЛЕ ответа: детектируем декодированные паттерны
        2b. ПОСЛЕ ответа: мониторим подозрительные 404
        2d. ПОСЛЕ ответа: мониторим подозрительные 200 к корню
        
        Args:
            request: HTTP запрос
            call_next: Следующий обработчик в цепочке
            
        Returns:
            Response: HTTP ответ от следующего обработчика
        """
        raw_path = request.url.path
        client_ip = request.client.host
        
        # ЭТАП 1: Детекция URL-encoded атак (ДО обработки запроса)
        # Проверяем оригинальный путь до декодирования URL
        for pattern in self.URL_ENCODED_PATTERNS:
            if pattern.lower() in raw_path.lower():
                security_logger.log_path_traversal_attempt(
                    ip=client_ip,
                    path=raw_path
                )
                break  # Достаточно одного лога на запрос
        
        # Обрабатываем запрос
        response = await call_next(request)
        
        # ЭТАП 2: Мониторинг после обработки (детекция последствий атак)
        
        # Проверка 2a: Декодированные паттерны Path Traversal (для ВСЕХ запросов)
        # Uvicorn мог декодировать %2e%2e в .., проверяем результат независимо от кода ответа
        for pattern in self.DECODED_PATTERNS:
            if pattern in raw_path:
                security_logger.log_path_traversal_attempt(
                    ip=client_ip,
                    path=raw_path
                )
                break  # Достаточно одного лога
        
        # Проверка 2b-2d: Мониторинг подозрительных кодов ответа (только 404/200)
        # Проверяем как 404, так и подозрительные 200 к корню
        if response.status_code in [404, 200]:
            
            # Проверка 2b: Подозрительные пути (только для 404)
            if response.status_code == 404:
                for suspicious_path in self.SUSPICIOUS_404_PATHS:
                    if raw_path.startswith(suspicious_path):
                        security_logger.log_invalid_request(
                            ip=client_ip,
                            endpoint=raw_path,
                            reason=f"Suspicious 404: system path access ({suspicious_path})"
                        )
                        break
                else:
                    # Выполнится только если break НЕ был вызван
                    # Проверка 2c: Подозрительный 404 к корню
                    if raw_path == '/' or raw_path == '':
                        user_agent = request.headers.get('user-agent', '').lower()
                        if any(tool in user_agent for tool in SECURITY_CLI_TOOLS) or not user_agent:
                            security_logger.log_invalid_request(
                                ip=client_ip,
                                endpoint=raw_path,
                                reason="Suspicious 404: root path with CLI tool (possible normalized path traversal)"
                            )
            
            # Проверка 2d: Подозрительный 200 к корню с CLI инструментом
            # Браузеры делают легитимные запросы к корню, CLI инструменты - подозрительно
            elif response.status_code == 200 and (raw_path == '/' or raw_path == ''):
                # Исключаем localhost (healthcheck скрипты и мониторинг)
                if client_ip not in self.WHITELIST_IPS:
                    user_agent = request.headers.get('user-agent', '').lower()
                    # Детектируем CLI инструменты
                    if any(tool in user_agent for tool in SECURITY_CLI_TOOLS) or not user_agent:
                        security_logger.log_invalid_request(
                            ip=client_ip,
                            endpoint=raw_path,
                            reason="Suspicious 200: root path access with CLI tool (possible normalized path traversal)"
                        )
        
        return response
