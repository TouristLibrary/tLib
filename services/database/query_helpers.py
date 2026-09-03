# Version 1.1 - 21.02.2026 00:00:00 GMT
# Query Helpers для построения SQL запросов
# Описание: Вспомогательные функции для обработки параметров поиска.
#           get_form_value() - безопасное извлечение и нормализация значения поля из form_data.
#           category_sort_key() - сортировка категорий по сложности (н/к -> б/к -> УТП -> 0-6).
#           extract_words_from_text() - извлечение слов из текста с поддержкой кириллицы и SQL LIKE символов.
#           escape_like_pattern() - обработка SQL LIKE паттернов (wildcards разрешены).
#           parse_route_field() - парсинг поля Маршрут для извлечения Шифр и ДопШифр.
#           get_category_index() - получение индекса категории в упорядоченном списке сложности.
#           Все функции являются чистыми (без побочных эффектов).

import re

from logging_config import app_logger


def get_form_value(form_data, key: str, log_value: bool = False) -> str:
    """
    Извлекает и нормализует значение поля из формы поиска.

    Args:
        form_data: dict-like объект с данными формы
        key: имя поля
        log_value: если True, логирует непустые значения через app_logger.debug

    Returns:
        str: нормализованное значение (пустая строка если поле отсутствует или ошибка)
    """
    try:
        value = form_data.get(key)
        if value is None:
            return ''
        result = value.strip() if isinstance(value, str) else str(value).strip()
        if result and log_value:
            app_logger.debug(f"    {key} = '{result}'")
        return result
    except Exception as e:
        app_logger.error(f"    ОШИБКА при получении {key}: {e}")
        return ''


def category_sort_key(cat):
    """
    Возвращает ключ для сортировки категорий по сложности похода.
    
    Порядок: н/к -> б/к -> УТП -> 0 -> 1 -> 1А -> 1Б -> ... -> 4Б -> с элементами 5 к.с. -> 5 -> ... -> 6
    
    Args:
        cat: Название категории (строка)
        
    Returns:
        tuple: Кортеж для сортировки (группа, числовой_ключ, суффикс)
    """
    cat_upper = cat.upper().strip()
    
    # Особые категории в начале (самые простые)
    if cat_upper == 'Н/К':
        return (0, 0, '')
    if cat_upper == 'Б/К':
        return (0, 1, '')
    if cat_upper == 'УТП':
        return (0, 2, '')
    
    # "с элементами 5 к.с." — перед 5 (между 4Б и 5)
    if 'ЭЛЕМЕНТ' in cat_upper:
        return (1, 499, cat)  # 499 = после 4, перед 5
    
    # Числовые категории: извлекаем число и суффикс
    match = re.match(r'^(\d+)\s*,?\s*(\d*)\s*([А-ЯA-Z\*]*)$', cat_upper)
    if match:
        main_num = int(match.group(1))
        sub_num = int(match.group(2)) if match.group(2) else 0
        suffix = match.group(3) or ''
        return (1, main_num * 100 + sub_num, suffix)
    
    # Остальные категории — в конец
    return (2, 0, cat)


def extract_words_from_text(text):
    """
    Извлекает слова из текста, удаляя разделители
    
    Текстом считаются:
    - Буквы: латиница (a-z, A-Z), кириллица (а-я, А-Я, ё, Ё)
    - Цифры: 0-9
    - Дефис: -
    - Специальные символы SQL LIKE: % и _
    
    Все остальные символы считаются разделителями и удаляются.
    
    Args:
        text: Входная строка
        
    Returns:
        list: Список слов (непустых строк без разделителей)
    """
    if not text:
        return []
    
    # Регулярное выражение: оставляем только буквы (латиница, кириллица), цифры, дефис и SQL LIKE символы
    # Заменяем все остальное на пробелы
    cleaned = re.sub(r'[^a-zA-Zа-яА-ЯёЁ0-9\-_%]', ' ', text)
    
    # Разбиваем по пробелам и фильтруем пустые строки
    words = [word for word in cleaned.split() if word]
    
    return words


def escape_like_pattern(word):
    """
    Экранирует специальные символы SQL LIKE, если они не должны использоваться как wildcards
    
    В SQLite для экранирования используется символ обратного слеша (\\)
    Но мы НЕ экранируем % и _, так как пользователь может их использовать намеренно
    
    Args:
        word: Слово для обработки
        
    Returns:
        str: Обработанное слово
    """
    # В данной реализации мы НЕ экранируем % и _, 
    # так как по требованию они должны работать как wildcards
    return word


def parse_route_field(value):
    """
    Парсит поле Маршрут для извлечения Шифр и ДопШифр из начала строки.
    
    Если строка начинается с числа — оно считается Шифром.
    Если после числа идёт "-" и подстрока без пробелов — она считается ДопШифром.
    Оставшаяся часть строки возвращается как маршрут для поиска по словам.
    
    Формат: "123-а остальной текст" или "123 остальной текст" или "текст без шифра"
    
    Args:
        value: Значение поля Маршрут из формы поиска
        
    Returns:
        dict: {'shifr': str|None, 'dopshifr': str|None, 'route': str}
              shifr - извлечённый шифр (только цифры) или None
              dopshifr - извлечённый дополнительный шифр или None
              route - оставшийся текст для поиска по словам
    """
    if not value or not value.strip():
        return {'shifr': None, 'dopshifr': None, 'route': ''}
    
    text = value.strip()
    
    # Паттерн: число в начале, опционально -суффикс (без пробелов), затем остаток
    # (\d+) - одна или более цифр (Шифр)
    # (?:-(\S+))? - опционально: дефис и непробельные символы (ДопШифр)
    # \s*(.*) - опционально пробелы и остаток строки (Маршрут)
    match = re.match(r'^(\d+)(?:-(\S+))?\s*(.*)', text)
    
    if match and match.group(1):
        return {
            'shifr': match.group(1),
            'dopshifr': match.group(2),  # None если нет дефиса
            'route': match.group(3).strip()
        }
    
    return {'shifr': None, 'dopshifr': None, 'route': text}


def get_category_index(category, category_list):
    """
    Возвращает индекс категории в упорядоченном списке
    
    Args:
        category: Значение категории (строка)
        category_list: Упорядоченный список всех категорий (из app.state)
        
    Returns:
        int: Индекс категории в списке или None если категория не найдена
    """
    if not category or (isinstance(category, str) and category.strip() == ''):
        return None
    try:
        # Пропускаем первый элемент "", если он есть
        clean_list = [c for c in category_list if c != '']
        return clean_list.index(category.strip())
    except (ValueError, AttributeError):
        return None
