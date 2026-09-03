# Version 3.1 - 25.03.2026
# Query Builder для построения SQL запросов
# Описание: Координатор построения SQL запросов для поиска в базе данных.
#           build_search_query() - главная функция-координатор, объединяет фильтры из модулей.
#           Делегирует построение фильтров специализированным модулям:
#           - query_filters.build_text_filters() - текстовые поля (Маршрут, Район, Автор)
#           - query_filters.build_category_filters() - категории с максимальной сложностью
#           - query_filters.build_date_filters() - годы, месяцы (цикличность), дата загрузки
#           Оставляет простые фильтры (Шифр, ДопШифр, РайонОбщий, Тип) в основной функции.
#           Все запросы параметризованы для защиты от SQL Injection.
#           Сортировка параметрическая: sortColumn и sortOrder из form_data.
#           Допустимые значения sortColumn и ORDER BY выражения задаются через SORT_CONFIGS (whitelist).

from logging_config import app_logger
from config import DATABASE_TABLE_NAME
from .query_filters import (
    build_text_filters,
    build_category_filters,
    build_date_filters
)
from .query_helpers import get_form_value

# Whitelist допустимых столбцов сортировки и соответствующих SQL-выражений ORDER BY.
# Имена столбцов из клиента никогда не вставляются напрямую в SQL.
# {order} подставляется как 'ASC' или 'DESC'.
SORT_CONFIGS = {
    'Год': 'Год {order}, МесяцС {order}',
    'Категория': 'MAX(CATEGORY_INDEX(КатегорияС), CATEGORY_INDEX(КатегорияПо)) {order}',
    'ДатаВремяЗагрузки': 'ДатаВремяЗагрузки {order}',
}
SORT_ORDERS = {'asc', 'desc'}
SORT_DEFAULT_COLUMN = 'Год'
SORT_DEFAULT_ORDER = 'desc'


def build_search_query(form_data, kategoria_list=None):
    """
    Строит SQL запрос на основе параметров формы поиска
    
    Создает параметризованный SQL запрос для безопасного поиска в базе данных.
    Все значения передаются через параметры для защиты от SQL Injection.
    
    Args:
        form_data: Данные формы (dict-like объект) с параметрами поиска
        kategoria_list: Объединённый упорядоченный список категорий (из app.state.kategoria_unified_list)
                   Поддерживаемые поля:
                   - Шифр: точное совпадение
                   - ДопШифр: точное совпадение или "нет" для пустых
                   - Маршрут, Автор: поиск по словам (все слова в любом порядке)
                   - Район: поиск по словам в полях Район и РайонОбщий (OR логика)
                   - РайонОбщий, Тип: точное совпадение
                   - КатегорияС, КатегорияПо: поиск по максимальной категории похода (по индексу сложности)
                   - ГодС, ГодПо (форма): проверка попадания поля Год в диапазон
                   - МесяцС, МесяцПо: пересечение интервалов месяцев с учетом NULL и цикличности
    
    Returns:
        tuple: (query, params) где
               query - строка SQL запроса с плейсхолдерами (?)
               params - список значений для подстановки в запрос
    
    Примечание:
        Использует функцию LOWER() для регистронезависимого поиска.
        Требуется регистрация пользовательской функции LOWER в SQLite
        для корректной работы с кириллицей (см. app.py, строка 832).
    """
    app_logger.debug("  build_search_query: начало")
    query = f"SELECT * FROM {DATABASE_TABLE_NAME} WHERE 1=1"
    params = []

    # Шифр (точное совпадение)
    shifr = get_form_value(form_data, 'Шифр', log_value=True)
    if shifr:
        query += " AND Шифр = ?"
        params.append(shifr)
        app_logger.debug(f"    Добавлен фильтр: Шифр = {shifr}")
    
    # ДопШифр с особой логикой
    # "нет" означает поиск записей без дополнительного шифра (NULL или пустая строка)
    dop_shifr = get_form_value(form_data, 'ДопШифр', log_value=True)
    if dop_shifr == 'нет':
        query += " AND (ДопШифр IS NULL OR TRIM(ДопШифр) = '')"
        app_logger.debug("    Добавлен фильтр: ДопШифр пустой")
    elif dop_shifr:
        query += " AND LOWER(ДопШифр) = LOWER(?)"
        params.append(dop_shifr)
        app_logger.debug(f"    Добавлен фильтр: LOWER(ДопШифр) = LOWER({dop_shifr})")
    
    # Справочные поля (регистронезависимое совпадение)
    # Эти поля содержат значения из выпадающих списков, но при указании через URL регистр может отличаться
    # КатегорияС и КатегорияПо обрабатываются отдельно как интервалы
    for field in ['РайонОбщий', 'Тип']:
        value = get_form_value(form_data, field, log_value=True)
        if value:
            query += f" AND LOWER({field}) = LOWER(?)"
            params.append(value)
            app_logger.debug(f"    Добавлен фильтр: LOWER({field}) = LOWER({value})")
    
    # Делегируем построение сложных фильтров специализированным модулям
    
    # Текстовые поля (Маршрут, Район, Автор)
    query, params = build_text_filters(form_data, query, params, shifr, dop_shifr)
    
    # Категории (КатегорияС, КатегорияПо)
    query, params = build_category_filters(form_data, query, params, kategoria_list)
    
    # Даты (Годы, Месяцы, ЗагруженоС)
    query, params = build_date_filters(form_data, query, params)
    
    # Параметрическая сортировка: читаем sortColumn и sortOrder из form_data
    sort_column = get_form_value(form_data, 'sortColumn') or SORT_DEFAULT_COLUMN
    sort_order = get_form_value(form_data, 'sortOrder') or SORT_DEFAULT_ORDER

    if sort_order.lower() not in SORT_ORDERS:
        sort_order = SORT_DEFAULT_ORDER
    if sort_column not in SORT_CONFIGS:
        sort_column = SORT_DEFAULT_COLUMN

    order_sql = SORT_CONFIGS[sort_column].format(order=sort_order.upper())
    query += f" ORDER BY {order_sql}"

    app_logger.debug(f"  build_search_query: завершено. Параметров: {len(params)}, ORDER BY {order_sql}")
    return query, params
    