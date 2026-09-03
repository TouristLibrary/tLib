# Version 3.0 - 26.12.2025 11:39:46 GMT
# File Service для работы с файлами
# Описание: Содержит функции для работы с именами файлов из ZIP архивов с поддержкой кириллицы:
#           - decode_zip_filename() - декодирует имена файлов с универсальной поддержкой кодировок
#           - _has_mojibake() - определяет наличие символов-кракозябр
#           - _is_valid_decoded() - проверяет качество декодирования
#           - is_macos_metadata_file() - фильтрует служебные файлы macOS
#           Поддерживает множественные кодировки (UTF-8, CP866, CP1251, Latin1) из конфигурации ZIP_ENCODINGS.
#           Автоматически исправляет неправильное декодирование CP437 в правильную кодировку.
#           Исправлена проблема с порядком проверки кодировок: CP866 (DOS) проверяется перед CP1251 (Windows).

from config import ZIP_ENCODINGS


def _has_mojibake(text: str) -> bool:
    """
    Определяет наличие символов-кракозябр (результат неправильного декодирования)
    
    Проверяет наличие символов, которые часто появляются при неправильном декодировании
    кириллицы через CP437:
    1. Символы блочной графики (U+2500 - U+257F): ─, │, ┌, ┐, └, ┘, ├, ┤, ┬, ┴, ┼, ═, ║, и т.д.
    2. Греческие буквы (U+0370 - U+03FF): α, β, γ, Γ, Δ, Φ, τ, σ и т.д.
       (появляются при декодировании CP866/CP1251 как CP437)
    
    Args:
        text: Текст для проверки
        
    Returns:
        bool: True если обнаружены кракозябры
    """
    for c in text:
        code = ord(c)
        # Символы блочной графики (появляются при декодировании UTF-8 как CP437)
        if 0x2500 <= code <= 0x257F:
            return True
        # Греческие буквы (появляются при декодировании CP866/CP1251 как CP437)
        if 0x0370 <= code <= 0x03FF:
            return True
    return False


def _is_valid_decoded(text: str) -> bool:
    """
    Проверяет, является ли декодированный текст правдоподобным
    
    Выполняет несколько проверок качества декодирования:
    - Отсутствие управляющих символов (кроме стандартных: \n, \r, \t)
    - Отсутствие суррогатных пар Unicode (признак неправильного декодирования)
    - Все символы в пределах BMP (Basic Multilingual Plane, < 0xFFFF)
    - Отсутствие блочной графики (кракозябр)
    
    Args:
        text: Декодированный текст для проверки
        
    Returns:
        bool: True если текст выглядит корректно декодированным
    """
    if not text:
        return False
    
    # Проверяем наличие управляющих символов (кроме переноса строки, возврата каретки, табуляции)
    # Символы с кодом < 32 (кроме разрешенных) указывают на проблемы с кодировкой
    if any(ord(c) < 32 and c not in '\n\r\t' for c in text):
        return False
    
    # Проверяем наличие суррогатных пар Unicode (U+D800 - U+DFFF)
    # Суррогаты - это признак неправильного декодирования UTF-16
    if any(0xD800 <= ord(c) <= 0xDFFF for c in text):
        return False
    
    # Проверяем, что символы в пределах BMP (Basic Multilingual Plane, 0xFFFF)
    if any(ord(c) > 0xFFFF for c in text):
        return False
    
    # Проверяем отсутствие блочной графики (должно быть False после успешного декодирования)
    if _has_mojibake(text):
        return False
    
    return True


def decode_zip_filename(filename):
    """
    Декодирует имена файлов из ZIP с универсальной поддержкой кириллицы
    
    ZIP архивы могут содержать имена файлов в различных кодировках.
    Python zipfile по умолчанию читает имена как CP437, что приводит к кракозябрам
    при работе с кириллицей.
    
    Функция использует двухэтапный алгоритм:
    1. Если имя уже декодировано (str), но содержит кракозябры - исправляет их
       через перекодирование CP437 -> [UTF-8, CP1251, CP866, Latin1]
    2. Если имя в bytes - пробует декодировать через все поддерживаемые кодировки
    
    Args:
        filename: Имя файла из ZIP архива (str или bytes)
        
    Returns:
        str: Правильно декодированное имя файла
        
    Примечание:
        Порядок кодировок в ZIP_ENCODINGS важен - сначала пробуются
        более вероятные кодировки (UTF-8 для macOS/Linux, CP1251 для Windows).
    """
    # ЭТАП 1: Если уже строка - проверяем на наличие кракозябр
    if isinstance(filename, str):
        # Проверяем наличие символов-кракозябр (блочная графика CP437)
        if _has_mojibake(filename):
            # Обнаружены кракозябры - пробуем исправить перекодированием
            # Перебираем все поддерживаемые кодировки из конфигурации
            for encoding in ZIP_ENCODINGS:
                try:
                    # Перекодируем: str (CP437 кракозябры) -> bytes (CP437) -> str (правильная кодировка)
                    decoded = filename.encode('cp437').decode(encoding)
                    
                    # Проверяем качество декодирования
                    if _is_valid_decoded(decoded):
                        # Успешное декодирование - возвращаем результат
                        return decoded
                except (UnicodeDecodeError, UnicodeEncodeError, LookupError):
                    # Эта кодировка не подошла, пробуем следующую
                    continue
        
        # Кракозябр нет или не удалось исправить - возвращаем как есть
        return filename
    
    # ЭТАП 2: Если bytes - пробуем декодировать с разными кодировками
    for encoding in ZIP_ENCODINGS:
        try:
            if isinstance(filename, bytes):
                # Прямое декодирование из bytes
                decoded = filename.decode(encoding)
            else:
                # Декодирование через latin1 (для случаев неправильной первичной кодировки)
                decoded = filename.encode('latin1').decode(encoding)
            
            # Проверяем качество декодирования
            if decoded and _is_valid_decoded(decoded):
                return decoded
        except (UnicodeDecodeError, UnicodeEncodeError, LookupError):
            # Эта кодировка не подошла, пробуем следующую
            continue
    
    # Если ни одна кодировка не подошла - возвращаем как строку
    # (лучше показать что-то, чем упасть с ошибкой)
    return str(filename)


def is_macos_metadata_file(filename: str) -> bool:
    """
    Проверяет, является ли файл служебным файлом macOS
    
    macOS автоматически создает служебные файлы при архивировании:
    - Папка __MACOSX/ - содержит метаданные о файлах
    - Файлы с префиксом ._ (dot-underscore) - расширенные атрибуты (resource fork)
    
    Эти файлы не нужны пользователям и должны фильтроваться при отображении
    содержимого архива.
    
    Args:
        filename: Имя файла или путь к файлу внутри архива
        
    Returns:
        bool: True если это служебный файл macOS, который нужно скрыть
        
    Примеры:
        >>> is_macos_metadata_file('__MACOSX/file.txt')
        True
        >>> is_macos_metadata_file('folder/__MACOSX/file.txt')
        True
        >>> is_macos_metadata_file('._file.txt')
        True
        >>> is_macos_metadata_file('folder/._file.txt')
        True
        >>> is_macos_metadata_file('normal_file.txt')
        False
    """
    return (
        filename.startswith('__MACOSX/') or     # Папка с метаданными в корне
        filename.startswith('._') or             # Файл с расширенными атрибутами в корне
        '/__MACOSX/' in filename or              # Папка с метаданными в подпапке
        '/._' in filename                        # Файл с расширенными атрибутами в подпапке
    )
