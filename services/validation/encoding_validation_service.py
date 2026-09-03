# Version 1.0 - 16.12.2025 13:06:34 GMT
# Encoding Validation Service для TlibWebApp
# Описание: Сервис для валидации кодировки JSON файлов.
#           Комбинированный подход: быстрая проверка всего файла + точная проверка полей при ошибке.
#           Проверяет: null bytes, суррогатные пары, невалидные Unicode символы, управляющие символы.
#           Функции: validate_file_encoding - быстрая проверка всего файла regex (~1-5 мс),
#           validate_string_encoding - детальная проверка одного поля с точным указанием позиции ошибки,
#           validate_json_encoding_detailed - проверка всех строковых полей JSON для детального отчета.
#           Используется в File Watcher для усиленной валидации на Этапе 3.

import re
from pathlib import Path
from typing import Tuple

from logging_config import app_logger
from config import MAX_STRING_LENGTH
from config import STRING_FIELDS_FOR_VALIDATION


def validate_file_encoding(file_path: Path) -> Tuple[bool, str]:
    """
    Быстрая проверка всего файла на проблемы с кодировкой (1-5 мс).
    
    Использует regex для поиска запрещенных символов:
    - Null bytes (\x00)
    - Суррогатные пары (U+D800 - U+DFFF)
    - Non-characters (U+FFFE, U+FFFF)
    - Управляющие символы (кроме \t, \n, \r)
    
    Args:
        file_path: Путь к файлу для проверки
        
    Returns:
        (is_valid: bool, error_msg: str)
        - is_valid: True если файл валиден
        - error_msg: Пустая строка при успехе или краткое описание ошибки
    """
    try:
        # Читаем файл как бинарный
        with open(file_path, 'rb') as f:
            raw_bytes = f.read()
        
        # 1. Проверяем, что это валидный UTF-8
        try:
            text = raw_bytes.decode('utf-8-sig', errors='strict')
        except UnicodeDecodeError as e:
            return False, f"Невалидный UTF-8: {e}"
        
        # 2. Проверяем на null bytes (быстро)
        if '\x00' in text:
            return False, "Файл содержит null bytes (\\x00)"
        
        # 3. Проверяем на запрещенные символы (быстро через regex)
        # Ищем: суррогаты (D800-DFFF), non-characters (FFFE, FFFF), 
        # управляющие символы (00-08, 0B, 0C, 0E-1F)
        forbidden = re.compile(r'[\uD800-\uDFFF\uFFFE\uFFFF\x00-\x08\x0B\x0C\x0E-\x1F]')
        match = forbidden.search(text)
        if match:
            char = match.group()
            pos = match.start()
            code = ord(char)
            return False, f"Запрещенный символ U+{code:04X} в позиции {pos}"
        
        return True, ""
        
    except Exception as e:
        error_msg = f"Ошибка чтения файла: {e}"
        app_logger.error(f"[VALIDATION] {error_msg}", exc_info=True)
        return False, error_msg


def validate_string_encoding(value: str, field_name: str) -> Tuple[bool, str]:
    """
    Детальная проверка строкового поля на проблемы с кодировкой.
    
    Возвращает точное сообщение об ошибке с номером позиции символа.
    
    Args:
        value: Строка для проверки
        field_name: Имя поля (для сообщения об ошибке)
        
    Returns:
        (is_valid: bool, error_msg: str)
        - is_valid: True если строка валидна
        - error_msg: Пустая строка при успехе или детальное описание ошибки
    """
    if not isinstance(value, str):
        return True, ""  # Не строка - не наша проблема
    
    if not value:
        return True, ""  # Пустая строка - OK
    
    # 1. Проверка на null bytes
    if '\x00' in value:
        pos = value.index('\x00')
        return False, f"{field_name}: содержит null byte (\\x00) в позиции {pos}"
    
    # 2. Проверка на суррогатные пары
    try:
        # Попытка закодировать обратно в UTF-8
        value.encode('utf-8', errors='strict')
    except UnicodeEncodeError as e:
        return False, f"{field_name}: невалидный Unicode (surrogate pair?): {e}"
    
    # 3. Проверка на невалидные Unicode символы
    for i, char in enumerate(value):
        code = ord(char)
        
        # Non-characters в Unicode
        if code in (0xFFFE, 0xFFFF):
            return False, f"{field_name}: содержит невалидный Unicode символ U+{code:04X} в позиции {i}"
        
        # Диапазон суррогатов (U+D800 - U+DFFF)
        if 0xD800 <= code <= 0xDFFF:
            return False, f"{field_name}: содержит суррогатный символ U+{code:04X} в позиции {i}"
        
        # Управляющие символы (кроме разрешенных)
        # Разрешаем: \t (9), \n (10), \r (13)
        if code < 32 and code not in (9, 10, 13):
            return False, f"{field_name}: содержит управляющий символ U+{code:04X} в позиции {i}"
    
    # 4. SQLite-специфичная проверка: максимальная длина
    # SQLite TEXT максимум ~1 млрд символов, но практично ограничить меньше
    if len(value) > MAX_STRING_LENGTH:
        return False, f"{field_name}: слишком длинная строка ({len(value)} символов, максимум {MAX_STRING_LENGTH:,})"
    
    return True, ""


def validate_json_encoding_detailed(json_data: dict) -> Tuple[bool, str]:
    """
    Проверяет все строковые поля JSON на проблемы с кодировкой.
    
    Вызывается только если validate_file_encoding() нашла проблему.
    Возвращает детальное сообщение с указанием конкретного поля и позиции.
    
    Args:
        json_data: Распарсенный JSON
        
    Returns:
        (is_valid: bool, error_msg: str)
        - is_valid: True если все поля валидны
        - error_msg: Пустая строка при успехе или детальное описание первой найденной ошибки
    """
    # Список всех строковых полей импортируется из config
    for field in STRING_FIELDS_FOR_VALIDATION:
        if field in json_data and json_data[field] is not None:
            is_valid, error = validate_string_encoding(json_data[field], field)
            if not is_valid:
                return False, error
    
    return True, ""
