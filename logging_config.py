# Version 1.7 - 21.06.2026 12:15:00 GMT
# Unified Logging System для TlibWebApp
# Описание: Центральная система логирования с гибридным форматом, читаемым человеком и ИИ. Формат логов:
#           [timestamp] LEVEL [req_id] function:line | key=value msg="quoted text".
#           Основные компоненты: HybridFormatter (форматтер с timestamp, level, request_id, structured data),
#           app_logger (главный логгер с 3 handlers), request_id_var (ContextVar для request_id в контексте запроса),
#           log_with_data() (логирование со структурированными данными), log_security_event() (события безопасности),
#           SecurityLogger (класс для удобного логирования событий безопасности),
#           parse_logfmt_fields() (парсер logfmt-строк; единая точка для admin_router и alerts/digest).
#           Файлы логов: logs/app.log (INFO+), logs/debug.log (DEBUG+), logs/critical.log (WARNING+).
#           Использует константы из config.py для размеров файлов и счетчиков.
# Изменения v1.6: добавлен SecurityLogger.log_email_quota_exceeded — корректная метка
#                 event_type=EMAIL_QUOTA для исчерпания дневного лимита request-link.
# Изменения v1.7: уточнён docstring log_email_quota_exceeded (убрана неточность про дайджест).

import os
import re
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from contextvars import ContextVar
from config import (
    REQUEST_ID_SHORT_LENGTH,
    LOG_APP_MAX_SIZE,
    LOG_DEBUG_MAX_SIZE,
    LOG_CRITICAL_MAX_SIZE,
    LOG_APP_BACKUP_COUNT,
    LOG_DEBUG_BACKUP_COUNT,
    LOG_CRITICAL_BACKUP_COUNT,
    LOG_DIRECTORY,
    LOG_FILE_APP,
    LOG_FILE_DEBUG,
    LOG_FILE_CRITICAL,
    LOG_CONSOLE_LEVEL,
    LOG_DEBUG_LEVEL
)

# ============================================================================
# CONTEXT VAR ДЛЯ REQUEST ID
# ============================================================================

# ContextVar для хранения request_id в контексте запроса
# Используется для связывания всех логов одного HTTP запроса
request_id_var: ContextVar[str] = ContextVar('request_id', default=None)


# ============================================================================
# HYBRID FORMATTER
# ============================================================================

class HybridFormatter(logging.Formatter):
    """
    Гибридный форматтер: читаемый человеком + структурированный для ИИ
    
    Формат: [timestamp] LEVEL [req_id] function:line | key=value msg="text"
    
    Компоненты:
    1. [timestamp] - временная метка UTC с миллисекундами (YYYY-MM-DD HH:MM:SS.mmm)
    2. LEVEL - уровень логирования (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    3. [req_id] - короткий request ID (8 символов) или [--------] если нет
    4. function:line - имя функции и номер строки
    5. | - разделитель
    6. key=value - структурированные данные в logfmt формате
    
    Структурированные данные:
    - key=value - для чисел, булевых значений, null
    - key="value" - для строк (кавычки экранируются)
    - Всегда присутствует: msg="основное сообщение"
    - Опционально: request_id=полный_uuid
    - Дополнительные поля из extra_data
    - exception_type и exception при наличии ошибки
    
    Преимущества:
    - Человек: видит главное сразу (timestamp, level, message)
    - ИИ: легко парсит regex или простым split
    - Однострочность: каждое событие = одна строка
    - Сортируемость: timestamp в начале
    """
    
    def format(self, record):
        """
        Форматирует запись лога в гибридный формат
        
        Args:
            record: LogRecord объект
            
        Returns:
            str: Отформатированная строка лога
        """
        # Временная метка UTC с миллисекундами
        timestamp = datetime.utcfromtimestamp(record.created).strftime(
            '%Y-%m-%d %H:%M:%S.%f'
        )[:-3]  # Обрезаем до миллисекунд (убираем последние 3 цифры микросекунд)
        
        # Request ID (короткий для читаемости)
        req_id = request_id_var.get()
        req_str = req_id[:REQUEST_ID_SHORT_LENGTH] if req_id else "-" * REQUEST_ID_SHORT_LENGTH
        
        # Базовая часть (читаемая человеком)
        base = f"[{timestamp}] {record.levelname:<8s} [{req_str}] {record.funcName}:{record.lineno}"
        
        # Сообщение
        message = record.getMessage()
        
        # Структурированные данные
        structured = []
        structured.append(f'msg="{self._escape_quotes(message)}"')
        
        # Добавляем request_id в структурированные данные (полный UUID)
        if req_id:
            structured.append(f'request_id={req_id}')
        
        # Добавляем extra_data если есть
        if hasattr(record, 'extra_data'):
            for key, value in record.extra_data.items():
                structured.append(self._format_value(key, value))
        
        # Добавляем информацию об ошибке если есть
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            # Заменяем переносы строк на \n для однострочности
            exc_text = exc_text.replace('\n', '\\n').replace('\r', '')
            structured.append(f'exception_type={record.exc_info[0].__name__}')
            structured.append(f'exception="{self._escape_quotes(exc_text)}"')
        
        # Собираем всё вместе
        return f"{base} | {' '.join(structured)}"
    
    def _format_value(self, key: str, value) -> str:
        """
        Форматирует пару ключ-значение в logfmt формат
        
        Args:
            key: Ключ
            value: Значение (любого типа)
            
        Returns:
            str: Отформатированная пара "key=value"
        """
        if value is None:
            return f'{key}=null'
        elif isinstance(value, bool):
            return f'{key}={str(value).lower()}'
        elif isinstance(value, (int, float)):
            return f'{key}={value}'
        else:
            # Строки в кавычках
            return f'{key}="{self._escape_quotes(str(value))}"'
    
    def _escape_quotes(self, text: str) -> str:
        """
        Экранирует кавычки в строке
        
        Args:
            text: Исходная строка
            
        Returns:
            str: Строка с экранированными кавычками
        """
        return text.replace('"', '\\"')


# ============================================================================
# НАСТРОЙКА ЛОГГЕРА
# ============================================================================

# Создаем директорию для логов
os.makedirs(LOG_DIRECTORY, exist_ok=True)

# Настройка основного логгера
app_logger = logging.getLogger('tlibwebapp')
app_logger.setLevel(logging.DEBUG)

# Удаляем существующие handlers если есть (на случай reload)
app_logger.handlers.clear()

# Handler 1: app.log - только успешные события (INFO)
# Фильтр блокирует WARNING+ для избежания дублирования с critical.log
app_handler = RotatingFileHandler(
    os.path.join(LOG_DIRECTORY, LOG_FILE_APP),
    maxBytes=LOG_APP_MAX_SIZE,
    backupCount=LOG_APP_BACKUP_COUNT,
    encoding='utf-8'
)
app_handler.setLevel(logging.INFO)
app_handler.addFilter(lambda record: record.levelno == logging.INFO)
app_handler.setFormatter(HybridFormatter())

# Handler 2: debug.log - отладочная информация (настраивается через LOG_DEBUG_LEVEL)
# OFF - handler не создаётся, debug.log не пишется
if LOG_DEBUG_LEVEL != "OFF":
    debug_handler = RotatingFileHandler(
        os.path.join(LOG_DIRECTORY, LOG_FILE_DEBUG),
        maxBytes=LOG_DEBUG_MAX_SIZE,
        backupCount=LOG_DEBUG_BACKUP_COUNT,
        encoding='utf-8'
    )
    debug_handler.setLevel(getattr(logging, LOG_DEBUG_LEVEL, logging.DEBUG))
    debug_handler.setFormatter(HybridFormatter())

# Handler 3: critical.log - только проблемы (WARNING+)
# Содержит: предупреждения безопасности (WARNING), ошибки (ERROR), критичные сбои (CRITICAL)
# Не содержит INFO для избежания дублирования с app.log
critical_handler = RotatingFileHandler(
    os.path.join(LOG_DIRECTORY, LOG_FILE_CRITICAL),
    maxBytes=LOG_CRITICAL_MAX_SIZE,
    backupCount=LOG_CRITICAL_BACKUP_COUNT,
    encoding='utf-8'
)
critical_handler.setLevel(logging.WARNING)
critical_handler.setFormatter(HybridFormatter())

# Handler 4: консоль
console_handler = logging.StreamHandler()
console_handler.setLevel(getattr(logging, LOG_CONSOLE_LEVEL))
console_handler.setFormatter(HybridFormatter())

# Добавляем все handlers к логгеру
app_logger.addHandler(app_handler)
if LOG_DEBUG_LEVEL != "OFF":
    app_logger.addHandler(debug_handler)
app_logger.addHandler(critical_handler)
app_logger.addHandler(console_handler)

# Handler для email-алертов при уровне CRITICAL.
# Импорт отложенный: CriticalMailHandler использует config и services, которые должны
# быть инициализированы к этому моменту. Ошибка импорта не роняет приложение.
try:
    from services.alerts.alerter import CriticalMailHandler as _CriticalMailHandler
    _critical_mail_handler = _CriticalMailHandler()
    app_logger.addHandler(_critical_mail_handler)
except Exception as _e:
    # Не критично — алерты по email недоступны, но логирование продолжает работать
    import warnings
    warnings.warn(f"[alerts] CriticalMailHandler не подключён: {_e}", stacklevel=1)


# ============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================================

def log_with_data(level, message, **extra_data):
    """
    Логирует сообщение с дополнительными структурированными данными
    
    Args:
        level: Уровень логирования (logging.INFO, logging.ERROR, etc.)
        message: Основное сообщение
        **extra_data: Дополнительные поля для структурированных данных
    
    Пример:
        log_with_data(logging.INFO, "Запрос обработан", 
                     endpoint="/api/search", results=123, time_ms=45.2)
        
    Результат в логе:
        [2025-11-22 20:50:41.123] INFO     [8a5b2c3d] function:42 | msg="Запрос обработан" 
        request_id=8a5b2c3d-1234-5678-90ab-cdef12345678 endpoint="/api/search" results=123 time_ms=45.2
    """
    extra = {'extra_data': extra_data}
    app_logger.log(level, message, extra=extra)


def log_security_event(event_type: str, ip: str, **details):
    """
    Логирует событие безопасности (автоматически попадет в critical.log)
    
    Все события безопасности логируются с уровнем WARNING, что гарантирует
    их попадание в critical.log для привлечения внимания администратора.
    
    Args:
        event_type: Тип события (PATH_TRAVERSAL, RATE_LIMIT, LARGE_FILE, etc.)
        ip: IP адрес клиента
        **details: Дополнительные детали события
    
    Пример:
        log_security_event("PATH_TRAVERSAL_ATTEMPT", 
                          ip="192.168.1.100", 
                          path="../../../etc/passwd",
                          threat_level="HIGH")
    
    Результат в critical.log:
        [2025-11-22 20:50:41.123] WARNING  [--------] security:85 | msg="🚨 Security: PATH_TRAVERSAL_ATTEMPT" 
        event_type=PATH_TRAVERSAL_ATTEMPT ip="192.168.1.100" path="../../../etc/passwd" 
        threat_level="HIGH" category=SECURITY
    """
    details['event_type'] = event_type
    details['ip'] = ip
    details['category'] = 'SECURITY'
    log_with_data(logging.WARNING, f"🚨 Security: {event_type}", **details)


# ============================================================================
# SECURITY LOGGER
# ============================================================================

class SecurityLogger:
    """
    Логгер для событий безопасности.
    Записывает подозрительные действия используя единую систему логирования.
    Все события автоматически попадают в critical.log (т.к. используется WARNING level).
    """
    
    def log_path_traversal_attempt(self, ip: str, path: str):
        """
        Логирует попытку Path Traversal атаки
        
        Args:
            ip: IP адрес атакующего
            path: Запрошенный путь
        """
        log_security_event(
            "PATH_TRAVERSAL_ATTEMPT",
            ip=ip,
            path=path,
            threat_level="HIGH"
        )
    
    def log_rate_limit_exceeded(self, ip: str):
        """
        Логирует превышение rate limit (слишком много запросов)
        
        Args:
            ip: IP адрес клиента
        """
        log_security_event(
            "RATE_LIMIT_EXCEEDED",
            ip=ip,
            threat_level="MEDIUM"
        )
    
    def log_invalid_request(self, ip: str, endpoint: str, reason: str):
        """
        Логирует невалидный запрос (некорректные параметры, ошибки валидации)
        
        Args:
            ip: IP адрес клиента
            endpoint: Endpoint, к которому был запрос
            reason: Причина отклонения запроса
        """
        log_security_event(
            "INVALID_REQUEST",
            ip=ip,
            endpoint=endpoint,
            reason=reason,
            threat_level="LOW"
        )
    
    def log_archive_size_exceeded(self, ip: str, archive: str, size: int):
        """
        Логирует запрос архива, превышающего допустимый размер
        
        Args:
            ip: IP адрес клиента
            archive: Имя архива
            size: Размер архива в байтах
        """
        size_mb = size / (1024 * 1024)  # bytes to MB
        log_security_event(
            "ARCHIVE_SIZE_EXCEEDED",
            ip=ip,
            archive=archive,
            size_bytes=size,
            size_mb=round(size_mb, 2),
            threat_level="MEDIUM"
        )
    
    def log_zip_bomb_detected(self, filename: str, ratio: float, compressed_mb: float, uncompressed_mb: float):
        """
        Логирует обнаружение потенциальной Zip Bomb атаки
        
        Args:
            filename: Имя ZIP файла
            ratio: Compression ratio
            compressed_mb: Размер сжатого (MB)
            uncompressed_mb: Размер распакованного (MB)
        """
        log_security_event(
            "ZIP_BOMB_DETECTED",
            ip="SYSTEM",
            filename=filename,
            ratio=ratio,
            compressed_mb=compressed_mb,
            uncompressed_mb=uncompressed_mb,
            threat_level="HIGH"
        )

    def log_email_quota_exceeded(self, ip: str, cap: int):
        """
        Логирует исчерпание дневного лимита писем входа (request-link).

        Пишет event_type=EMAIL_QUOTA в critical.log, что позволяет
        admin-панели и анализу логов корректно классифицировать событие.
        Немедленное письмо администратору отправляется отдельно через
        send_admin_alert("EMAIL_QUOTA") (уровень URGENT, троттлинг 30 мин);
        в суточный дайджест (секция ATTENTION) это событие не попадает.
        """
        log_security_event(
            "EMAIL_QUOTA",
            ip=ip,
            daily_cap=cap,
            threat_level="MEDIUM"
        )


# Глобальный экземпляр логгера безопасности
# Импортируется в других модулях: from logging_config import security_logger
security_logger = SecurityLogger()


# ============================================================================
# ПАРСЕР LOGFMT
# ============================================================================

# Регулярка для извлечения key=value пар из logfmt-строк формата critical.log.
# Поддерживает quoted-значения с экранированием (\"...\") и unquoted СЛОВА.
_LOGFMT_KV_RE = re.compile(r'(\w+)=(?:"([^"\\]*(?:\\.[^"\\]*)*)"|(\S+))')


def parse_logfmt_fields(text: str) -> dict[str, str]:
    """
    Извлекает key=value пары из logfmt-строки (или её подстроки fields).

    Поддерживает quoted-значения с экранированием и unquoted токены.
    Используется services/admin/status_service.collect_security и alerts/digest.parse_critical_log
    вместо локальных приватных парсеров.

    Args:
        text: Logfmt-строка, например: 'msg="Hello" event_type=FOO ip="1.2.3.4"'

    Returns:
        dict[str, str]: словарь полей; quoted-значения без кавычек.
    """
    result: dict[str, str] = {}
    for m in _LOGFMT_KV_RE.finditer(text):
        key = m.group(1)
        result[key] = m.group(2) if m.group(2) is not None else m.group(3)
    return result


# ============================================================================
# ЭКСПОРТ
# ============================================================================

__all__ = [
    'app_logger',
    'security_logger',
    'log_with_data',
    'request_id_var',
    'parse_logfmt_fields',
]
