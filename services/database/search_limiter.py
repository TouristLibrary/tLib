# Version 1.3 - 21.02.2026 00:00:00 GMT
# Search Limiter Service для TlibWebApp
# Описание: Сервис ограничения параллельных тяжёлых поисковых запросов. Содержит класс HeavyQueryLimiter
#           для управления очередью тяжёлых запросов через asyncio.Semaphore с отслеживанием размера очереди
#           и логированием Warning при превышении порога. Функция is_light_query() определяет лёгкие запросы
#           по эвристике (наличие селективных фильтров: Шифр, ДопШифр, Автор, РайонОбщий, Маршрут >5 символов,
#           Район >5 символов), позволяя пропускать их без COUNT проверки.

from asyncio import Semaphore, Lock
import logging
from logging_config import log_with_data
from config import SEARCH_EXACT_FIELDS, SEARCH_LENGTH_FIELDS


class HeavyQueryLimiter:
    """
    Лимитер для ограничения параллельных тяжёлых запросов.
    
    Использует asyncio.Semaphore для ограничения количества одновременно
    выполняющихся тяжёлых запросов. Отслеживает размер очереди ожидающих
    и логирует Warning при превышении порога.
    
    Attributes:
        semaphore: Семафор для ограничения параллельных запросов
        queue_warning_size: Порог для логирования Warning
        waiting_count: Текущее количество ожидающих запросов
        lock: Блокировка для атомарного изменения waiting_count
    """
    
    def __init__(self, max_concurrent: int, queue_warning_size: int):
        """
        Инициализация лимитера.
        
        Args:
            max_concurrent: Максимальное количество параллельных тяжёлых запросов
            queue_warning_size: Порог очереди для логирования Warning
        """
        self.semaphore = Semaphore(max_concurrent)
        self.queue_warning_size = queue_warning_size
        self.waiting_count = 0
        self.lock = Lock()
        self.max_concurrent = max_concurrent
    
    async def acquire(self):
        """
        Захватить слот для выполнения тяжёлого запроса.
        
        Увеличивает счётчик ожидающих, проверяет превышение порога очереди
        (логирует Warning если превышен), затем ожидает освобождения семафора.
        """
        async with self.lock:
            self.waiting_count += 1
            current_waiting = self.waiting_count
        
        if current_waiting > self.queue_warning_size:
            log_with_data(
                logging.WARNING,
                "Heavy query queue overflow",
                queue_size=current_waiting,
                threshold=self.queue_warning_size,
                max_concurrent=self.max_concurrent
            )
        
        await self.semaphore.acquire()
    
    async def release(self):
        """
        Освободить слот после выполнения тяжёлого запроса.
        
        Освобождает семафор и уменьшает счётчик ожидающих.
        """
        self.semaphore.release()
        
        async with self.lock:
            self.waiting_count -= 1


def is_light_query(form_data: dict, min_length: int) -> bool:
    """
    Проверяет, является ли запрос заведомо лёгким по эвристике.
    
    Лёгкий запрос — если задано хотя бы одно из селективных полей
    (определяется константами SEARCH_EXACT_FIELDS и SEARCH_LENGTH_FIELDS из config).
    
    Args:
        form_data: Словарь с данными формы поиска
        min_length: Минимальная длина для полей из SEARCH_LENGTH_FIELDS
        
    Returns:
        bool: True если запрос лёгкий, False если требуется проверка COUNT
    """
    for field in SEARCH_EXACT_FIELDS:
        value = form_data.get(field)
        if value and str(value).strip():
            return True
    
    for field in SEARCH_LENGTH_FIELDS:
        value = form_data.get(field)
        if value and len(str(value).strip()) > min_length:
            return True
    
    return False
