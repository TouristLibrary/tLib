# Version 1.8 - 22.01.2026
# Middleware для Rate Limiting
# Описание: Ограничивает количество запросов с одного IP адреса для защиты от DoS атак и брутфорса.
#           Отслеживает количество запросов с каждого IP адреса в окне времени, блокирует API запросы при превышении лимита
#           (возвращает HTTP 429 Too Many Requests). Для статических файлов применяет два уровня защиты:
#           1) Лимит одновременных соединений (MAX_CONCURRENT_STATIC_CONNECTIONS) - только для тяжелых файлов (PDF, архивы)
#           2) Жёсткий лимит запросов в минуту (RATE_LIMIT_STATIC_HARD_THRESHOLD) - для всех статических файлов
#           Легкие файлы (JS/CSS) исключены из concurrent limit для поддержки параллельной загрузки ES модулей.
#           Это защищает от массовых скачиваний и злоупотребления трафиком, не мешая легитимным PDF Range-запросам.
#           Автоматически очищает устаревшие записи для экономии памяти. Использует настройки из config.py.

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import time
from collections import defaultdict
from typing import Dict, Tuple
from logging_config import app_logger, security_logger
from config import (
    RATE_LIMIT_CLEANUP_INTERVAL,
    RATE_LIMIT_WINDOW_SECONDS,
    RATE_LIMIT_STATIC_WARNING_THRESHOLD,
    RATE_LIMIT_STATIC_HARD_THRESHOLD,
    RATE_LIMIT_REQUESTS_PER_MINUTE,
    MAX_CONCURRENT_STATIC_CONNECTIONS,
    LOCAL_ARCHIVE_PATH,
    STATIC_DIRS,
    FAVICON_URL_PATH,
    RETRY_AFTER_CONCURRENT_SECONDS,
    RATE_LIMIT_LIGHT_STATIC_PATHS
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting middleware для защиты от DoS атак.
    Ограничивает количество запросов с одного IP адреса в единицу времени.
    
    Для API endpoints: блокирует при превышении RATE_LIMIT_REQUESTS_PER_MINUTE.
    Для статических файлов применяет разделение на категории:
        - Легкие (JS/CSS): только hard limit (5000 запросов/мин)
        - Тяжелые (PDF, архивы): hard limit + concurrent limit (10 одновременных соединений)
    Это позволяет ES модулям загружаться параллельно при сохранении защиты от DDoS.
    """
    
    def __init__(self, app, requests_per_minute: int = None):
        """
        Инициализация middleware
        
        Args:
            app: FastAPI приложение
            requests_per_minute: Максимальное количество запросов в минуту с одного IP
                                Если None, используется значение из config.py
        """
        super().__init__(app)
        
        # Словарь для хранения информации о запросах к API: IP -> (timestamp, count)
        # timestamp - время первого запроса в текущей минуте
        # count - количество запросов в текущей минуте
        self.requests: Dict[str, Tuple[float, int]] = defaultdict(lambda: (time.time(), 0))
        
        # Словарь для отслеживания запросов к статическим файлам: IP -> (timestamp, count)
        # Используется для мониторинга подозрительной активности без блокировки
        self.static_requests: Dict[str, Tuple[float, int]] = defaultdict(lambda: (time.time(), 0))
        
        # Словарь для отслеживания активных соединений к статическим файлам: IP -> count
        # Используется для ограничения параллельных скачиваний
        self.active_connections: Dict[str, int] = defaultdict(int)
        
        # Настройки
        if requests_per_minute is None:
            requests_per_minute = RATE_LIMIT_REQUESTS_PER_MINUTE
        self.requests_per_minute = requests_per_minute
        self.cleanup_interval = RATE_LIMIT_CLEANUP_INTERVAL
        self.last_cleanup = time.time()
        
        # Пути, которые исключаются из rate limiting
        # (статические файлы для поддержки PDF Range запросов)
        # Генерируется динамически из STATIC_DIRS в config.py
        self.excluded_paths = (
            [f'{LOCAL_ARCHIVE_PATH}/'] +
            [f'/{d}/' for d in STATIC_DIRS] +
            [FAVICON_URL_PATH]
        )
        
        # Пути без ограничения concurrent (JS/CSS - маленькие, безопасны)
        # Для них применяется только hard limit, без concurrent limit
        self.light_static_paths = RATE_LIMIT_LIGHT_STATIC_PATHS
        
        # Пороги для статических файлов
        self.static_warning_threshold = RATE_LIMIT_STATIC_WARNING_THRESHOLD
        self.static_hard_threshold = RATE_LIMIT_STATIC_HARD_THRESHOLD
        self.max_concurrent_connections = MAX_CONCURRENT_STATIC_CONNECTIONS
    
    async def dispatch(self, request: Request, call_next):
        """
        Обработка каждого запроса с проверкой rate limit
        
        Args:
            request: HTTP запрос
            call_next: Следующий обработчик в цепочке
            
        Returns:
            Response: HTTP ответ (либо от следующего обработчика, либо 429 при превышении лимита)
        """
        request_path = request.url.path
        client_ip = request.client.host
        current_time = time.time()
        
        # Периодическая очистка старых записей для экономии памяти
        if current_time - self.last_cleanup > self.cleanup_interval:
            self._cleanup_old_requests(current_time)
            self.last_cleanup = current_time
        
        # Проверяем, не является ли это запросом к статическим файлам
        is_static = any(request_path.startswith(excluded) for excluded in self.excluded_paths)
        
        if is_static:
            # Проверяем, является ли это "легким" статическим файлом (JS/CSS)
            is_light_static = any(request_path.startswith(p) for p in self.light_static_paths)
            
            # Проверка лимита одновременных соединений ТОЛЬКО для тяжелых файлов (PDF, архивы)
            # JS/CSS пропускаем, так как они маленькие и загружаются параллельно браузером
            if not is_light_static:
                if self.active_connections[client_ip] >= self.max_concurrent_connections:
                    security_logger.log_invalid_request(
                        client_ip,
                        request_path,
                        f"Too many concurrent connections: {self.active_connections[client_ip]}"
                    )
                    return JSONResponse(
                        {"error": "Too many concurrent connections to static files"},
                        status_code=429,
                        headers={"Retry-After": str(RETRY_AFTER_CONCURRENT_SECONDS)}
                    )
                
                # Увеличиваем счётчик активных соединений только для тяжелых файлов
                self.active_connections[client_ip] += 1
            
            try:
                # Для статических файлов: отслеживаем активность и применяем жёсткий лимит
                # Hard limit применяется для ВСЕХ статических файлов (включая JS/CSS)
                static_timestamp, static_count = self.static_requests[client_ip]
                
                if current_time - static_timestamp > RATE_LIMIT_WINDOW_SECONDS:
                    # Прошло окно времени - сбрасываем счетчик
                    self.static_requests[client_ip] = (current_time, 1)
                else:
                    # Увеличиваем счетчик
                    new_count = static_count + 1
                    self.static_requests[client_ip] = (static_timestamp, new_count)
                    
                    # Логируем подозрительную активность при достижении порога предупреждения
                    if new_count == self.static_warning_threshold:
                        security_logger.log_invalid_request(
                            client_ip,
                            request_path,
                            f"High volume of static file requests: {new_count}/min"
                        )
                    
                    # Жёсткий лимит - блокируем при превышении
                    if new_count > self.static_hard_threshold:
                        security_logger.log_rate_limit_exceeded(client_ip)
                        return JSONResponse(
                            {"error": "Too many requests to static files. Please try again later."},
                            status_code=429,
                            headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)}
                        )
                
                # Обрабатываем запрос
                response = await call_next(request)
                return response
            finally:
                # Уменьшаем счётчик активных соединений только для тяжелых файлов
                if not is_light_static:
                    self.active_connections[client_ip] = max(0, self.active_connections[client_ip] - 1)
        
        # Для API endpoints применяем стандартный rate limiting
        timestamp, count = self.requests[client_ip]
        
        # Если прошло окно времени с момента первого запроса, сбрасываем счетчик
        if current_time - timestamp > RATE_LIMIT_WINDOW_SECONDS:
            self.requests[client_ip] = (current_time, 1)
        else:
            # Проверяем, не превышен ли лимит
            if count >= self.requests_per_minute:
                # Лимит превышен - логируем и возвращаем ошибку 429 Too Many Requests
                security_logger.log_rate_limit_exceeded(client_ip)
                return JSONResponse(
                    {"error": "Too many requests. Please try again later."},
                    status_code=429,
                    headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)}
                )
            
            # Лимит не превышен - увеличиваем счетчик
            self.requests[client_ip] = (timestamp, count + 1)
        
        # Продолжаем обработку запроса
        response = await call_next(request)
        return response
    
    def _cleanup_old_requests(self, current_time: float):
        """
        Удаляет записи о запросах старше 5 минут для экономии памяти
        
        Args:
            current_time: Текущее время (timestamp)
        """
        # Находим IP адреса с устаревшими записями для API requests
        to_delete = [
            ip for ip, (timestamp, _) in self.requests.items()
            if current_time - timestamp > self.cleanup_interval
        ]
        
        # Удаляем устаревшие записи для API
        for ip in to_delete:
            del self.requests[ip]
        
        # Находим IP адреса с устаревшими записями для static requests
        to_delete_static = [
            ip for ip, (timestamp, _) in self.static_requests.items()
            if current_time - timestamp > self.cleanup_interval
        ]
        
        # Удаляем устаревшие записи для статики
        for ip in to_delete_static:
            del self.static_requests[ip]
        
        # Находим IP адреса с нулевыми активными соединениями для очистки
        to_delete_connections = [
            ip for ip, count in self.active_connections.items()
            if count == 0
        ]
        
        # Удаляем записи с нулевыми соединениями
        for ip in to_delete_connections:
            del self.active_connections[ip]
        
        # Логируем количество очищенных записей
        if to_delete or to_delete_static or to_delete_connections:
            app_logger.info(f"[Rate Limiter] Очищено {len(to_delete)} API, {len(to_delete_static)} статических записей и {len(to_delete_connections)} соединений")
