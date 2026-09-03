# Version 1.0 - 25.12.2025 16:05:23 GMT
# Query Filters для построения SQL запросов
# Описание: Функции построения SQL фильтров для поиска в базе данных.
#           build_text_filters() - фильтры для текстовых полей (Маршрут, Район, Автор) с поиском по словам.
#           build_category_filters() - фильтры для категорий с определением максимальной категории похода.
#           build_date_filters() - фильтры для дат (годы, месяцы с цикличностью, дата загрузки).
#           Все функции принимают query и params, модифицируют их и возвращают обновленные.
#           Используют параметризованные запросы для защиты от SQL Injection.

from logging_config import app_logger
from config import YEAR_MIN, YEAR_MAX, MONTH_MIN, MONTH_MAX
from .query_helpers import (
    get_form_value,
    extract_words_from_text,
    escape_like_pattern,
    parse_route_field,
    get_category_index
)


def build_text_filters(form_data, query, params, shifr, dop_shifr):
    """
    Строит фильтры для текстовых полей: Маршрут, Район, Автор.
    
    Особенности:
    - Маршрут: извлекает Шифр и ДопШифр из начала строки, затем поиск по словам
    - Район: поиск одновременно в полях Район и РайонОбщий (OR логика)
    - Автор: стандартный поиск по словам (все слова должны присутствовать)
    
    Args:
        form_data: Данные формы поиска
        query: SQL запрос (строка)
        params: Список параметров для запроса
        shifr: Значение Шифр из основной формы (для проверки дублирования)
        dop_shifr: Значение ДопШифр из основной формы (для проверки дублирования)
        
    Returns:
        tuple: (query, params) с добавленными фильтрами
    """
    # Текстовые поля с LIKE для частичного совпадения
    # LOWER() используется для регистронезависимого поиска
    # Поиск происходит по всем словам (в любом порядке)
    text_fields = ['Маршрут', 'Район', 'Автор']
    for field in text_fields:
        value = get_form_value(form_data, field)
        if value:
            # Специальная обработка для поля Маршрут: извлечение Шифр/ДопШифр из начала строки
            if field == 'Маршрут':
                parsed = parse_route_field(value)
                
                # Добавляем условие по Шифр (если извлечён и поле формы Шифр пустое)
                if parsed['shifr'] and not shifr:
                    query += " AND Шифр = ?"
                    params.append(parsed['shifr'])
                    app_logger.debug(f"    Добавлен фильтр из Маршрут: Шифр = {parsed['shifr']}")
                    
                    # Если ДопШифр НЕ указан — искать только записи с пустым ДопШифром
                    if not parsed['dopshifr'] and not dop_shifr:
                        query += " AND (ДопШифр IS NULL OR TRIM(ДопШифр) = '')"
                        app_logger.debug("    Добавлен фильтр из Маршрут: ДопШифр пустой")
                
                # Добавляем условие по ДопШифр (если извлечён и поле формы ДопШифр пустое)
                if parsed['dopshifr'] and not dop_shifr:
                    query += " AND LOWER(ДопШифр) = LOWER(?)"
                    params.append(parsed['dopshifr'])
                    app_logger.debug(f"    Добавлен фильтр из Маршрут: LOWER(ДопШифр) = LOWER({parsed['dopshifr']})")
                
                # Поиск по оставшемуся тексту маршрута (если не пустой)
                if parsed['route']:
                    words = extract_words_from_text(parsed['route'])
                    for word in words:
                        escaped_word = escape_like_pattern(word)
                        query += " AND LOWER(Маршрут) LIKE LOWER(?)"
                        params.append(f"%{escaped_word}%")
                        app_logger.debug(f"    Добавлен фильтр: LOWER(Маршрут) LIKE LOWER(%{escaped_word}%)")
                elif not parsed['shifr']:
                    app_logger.debug(f"    Поле Маршрут: не найдено валидных данных в '{value}'")
            elif field == 'Район':
                # Специальная логика для поля Район: поиск по Район и РайонОбщий
                words = extract_words_from_text(value)
                
                if words:
                    # Каждое слово должно быть найдено ИЛИ в Район ИЛИ в РайонОбщий
                    for word in words:
                        escaped_word = escape_like_pattern(word)
                        query += " AND (LOWER(Район) LIKE LOWER(?) OR LOWER(РайонОбщий) LIKE LOWER(?))"
                        params.append(f"%{escaped_word}%")
                        params.append(f"%{escaped_word}%")
                        app_logger.debug(f"    Добавлен фильтр: LOWER(Район) LIKE LOWER(%{escaped_word}%) OR LOWER(РайонОбщий) LIKE LOWER(%{escaped_word}%)")
                else:
                    app_logger.debug(f"    Поле Район: не найдено валидных слов в '{value}'")
            else:
                # Стандартная логика для поля Автор
                words = extract_words_from_text(value)
                
                if words:
                    # Для каждого слова добавляем условие LIKE
                    # Все слова должны присутствовать (AND), но в любом порядке
                    for word in words:
                        escaped_word = escape_like_pattern(word)
                        query += f" AND LOWER({field}) LIKE LOWER(?)"
                        params.append(f"%{escaped_word}%")
                        app_logger.debug(f"    Добавлен фильтр: LOWER({field}) LIKE LOWER(%{escaped_word}%)")
                else:
                    app_logger.debug(f"    Поле {field}: не найдено валидных слов в '{value}'")
    
    return query, params


def build_category_filters(form_data, query, params, kategoria_list):
    """
    Строит фильтры для категорий похода (КатегорияС и КатегорияПо).
    
    Логика: из двух категорий похода определяется максимальная по сложности
    и проверяется её вхождение в диапазон поиска.
    
    Правила обработки:
    1. Если у похода есть только КатегорияС - она считается максимальной
    2. Если у похода есть только КатегорияПо - она считается максимальной
    3. Если обе заполнены - выбирается та, у которой больше индекс в списке сложности
    4. Если в форме КатегорияПо не указана, считается максимальной (последняя в списке)
    5. Если в форме КатегорияС не указана, считается минимальной (первая в списке)
    
    Args:
        form_data: Данные формы поиска
        query: SQL запрос (строка)
        params: Список параметров для запроса
        kategoria_list: Упорядоченный список категорий по сложности
        
    Returns:
        tuple: (query, params) с добавленными фильтрами
    """
    # Категории (поиск по максимальной категории похода)
    cat_from = get_form_value(form_data, 'КатегорияС')
    cat_to = get_form_value(form_data, 'КатегорияПо')
    
    if (cat_from or cat_to) and kategoria_list:
        # Используем объединённый список категорий (убираем пустые значения)
        category_order = [c for c in kategoria_list if c != '']
        app_logger.info(f"    category_order ({len(category_order)}): {category_order}")
        
        if category_order:
            # Определяем границы интервала из формы поиска
            # Если КатегорияПо не указана, считаем максимальной (последняя в списке)
            # Если КатегорияС не указана, считаем минимальной (первая в списке)
            form_cat_from_idx = get_category_index(cat_from, kategoria_list) if cat_from else 0
            form_cat_to_idx = get_category_index(cat_to, kategoria_list) if cat_to else len(category_order) - 1
            app_logger.info(f"    form_cat_from_idx={form_cat_from_idx}, form_cat_to_idx={form_cat_to_idx}")
            
            if form_cat_from_idx is not None and form_cat_to_idx is not None:
                # Вычисляем список допустимых категорий из интервала
                allowed_categories = category_order[form_cat_from_idx : form_cat_to_idx + 1]
                app_logger.info(f"    allowed_categories ({len(allowed_categories)}): {allowed_categories}")
                
                # Проверка на пустой интервал (from > to)
                if not allowed_categories:
                    app_logger.warning(f"    Пустой интервал категорий (from > to): from_idx={form_cat_from_idx}, to_idx={form_cat_to_idx}")
                    # Добавляем условие, которое не найдёт ничего
                    query += " AND 1=0"
                else:
                    # Генерируем placeholders для IN()
                    placeholders = ', '.join(['?'] * len(allowed_categories))
                    
                    # Отсекаем отчеты, где обе категории NULL или пустые строки
                    # Определяем максимальную категорию по индексу сложности и проверяем только её
                    # CATEGORY_INDEX() - пользовательская функция, возвращающая индекс категории в списке сложности
                    query += f"""
                        AND (
                            (КатегорияС IS NOT NULL AND TRIM(КатегорияС) != '') 
                            OR (КатегорияПо IS NOT NULL AND TRIM(КатегорияПо) != '')
                        )
                        AND (
                            CASE 
                                WHEN (КатегорияС IS NULL OR TRIM(КатегорияС) = '') THEN КатегорияПо
                                WHEN (КатегорияПо IS NULL OR TRIM(КатегорияПо) = '') THEN КатегорияС
                                WHEN CATEGORY_INDEX(КатегорияС) > CATEGORY_INDEX(КатегорияПо) THEN КатегорияС
                                ELSE КатегорияПо
                            END
                        ) IN ({placeholders})
                    """
                    # Добавляем параметры один раз (только для одного IN)
                    params.extend(allowed_categories)
                    
                    form_cat_from_name = category_order[form_cat_from_idx]
                    form_cat_to_name = category_order[form_cat_to_idx]
                    app_logger.debug(f"    Добавлен фильтр категорий по MAX IN(): [{form_cat_from_name}, {form_cat_to_name}], допустимых: {len(allowed_categories)}")
            else:
                app_logger.warning(f"    Некорректное значение категории: КатегорияС={cat_from}, КатегорияПо={cat_to}")
        else:
            app_logger.warning("    Список категорий пуст, фильтрация по категориям пропущена")
    elif cat_from or cat_to:
        app_logger.warning("    Список категорий не передан, фильтрация по категориям пропущена")
    
    return query, params


def build_date_filters(form_data, query, params):
    """
    Строит фильтры для дат: годы, месяцы (с цикличностью), дата загрузки.
    
    Особенности:
    - Годы: простая проверка попадания в диапазон [ГодС, ГодПо]
    - Месяцы: пересечение интервалов с учетом цикличности (Декабрь-Май = 12-5)
    - Дата загрузки: фильтр "Загружено с" (ISO datetime формат)
    
    Args:
        form_data: Данные формы поиска
        query: SQL запрос (строка)
        params: Список параметров для запроса
        
    Returns:
        tuple: (query, params) с добавленными фильтрами
    """
    # Годы (проверка попадания в диапазон)
    # Логика: проверяем, попадает ли поле Год из БД в диапазон [ГодС, ГодПо] из формы поиска
    # Правила обработки:
    # - Если в форме указан только ГодС, диапазон от ГодС до бесконечности (YEAR_MAX)
    # - Если в форме указан только ГодПо, диапазон от минус бесконечности (YEAR_MIN) до ГодПо
    # - Если в отчете Год равен NULL, отчет отсекается при поиске по годам
    year_from = get_form_value(form_data, 'ГодС')
    year_to = get_form_value(form_data, 'ГодПо')
    
    if year_from or year_to:
        try:
            # Определяем границы диапазона из формы поиска
            form_year_from = int(year_from) if year_from else YEAR_MIN
            form_year_to = int(year_to) if year_to else YEAR_MAX
            
            # Проверяем попадание поля Год в диапазон
            query += " AND Год IS NOT NULL AND Год >= ? AND Год <= ?"
            params.append(form_year_from)
            params.append(form_year_to)
            
            app_logger.debug(f"    Добавлен фильтр по годам: Год в диапазоне [{form_year_from}, {form_year_to}]")
            
        except ValueError as e:
            app_logger.warning(f"    Некорректное значение года: ГодС={year_from}, ГодПо={year_to}")
    
    # Месяцы (пересечение интервалов с обработкой NULL и цикличности)
    # Аналогично годам, но с учетом цикличности месяцев (после 12 идет 1)
    # - Оба месяца NULL → отчет отсекается
    # - Один месяц NULL → приравнивается к известному месяцу
    # - Нормальный интервал: МесяцС <= МесяцПо (Март-Июнь = 3-6)
    # - Циклический интервал: МесяцС > МесяцПо (Декабрь-Май = 12-5, зимние походы)
    # Пересечение: оба нормальные (стандартная проверка), форма циклическая (report_from >= form_from OR report_to <= form_to),
    # отчет циклический (form_from >= report_from OR form_to <= report_to), оба циклические (всегда TRUE)
    month_from = get_form_value(form_data, 'МесяцС')
    month_to = get_form_value(form_data, 'МесяцПо')
    
    if month_from or month_to:
        try:
            # Определяем границы интервала из формы поиска
            # Если МесяцПо не указан, считаем до конца года (MONTH_MAX)
            # Если МесяцС не указан, считаем с начала года (MONTH_MIN)
            form_month_from = int(month_from) if month_from else MONTH_MIN
            form_month_to = int(month_to) if month_to else MONTH_MAX
            
            # Определяем, циклический ли интервал формы
            form_is_cyclic = form_month_from > form_month_to
            
            # Отсекаем отчеты, где оба месяца NULL или пустые строки
            query += """
                AND (
                    (МесяцС IS NOT NULL AND TRIM(МесяцС) != '') 
                    OR (МесяцПо IS NOT NULL AND TRIM(МесяцПо) != '')
                )
            """
            
            # Вычисляем реальные границы интервала отчета с учетом NULL
            # и проверяем пересечение с учетом цикличности
            query += """
                AND (
            """
            
            if form_is_cyclic:
                # СЛУЧАЙ 1: Интервал формы циклический (form_from > form_to)
                # Пересечение если:
                # - Отчет нормальный: report_from >= form_from OR report_to <= form_to
                # - Отчет циклический: всегда TRUE
                query += """
                    (
                        CASE 
                            WHEN МесяцС IS NULL OR TRIM(МесяцС) = '' THEN CAST(МесяцПо AS INTEGER)
                            ELSE CAST(МесяцС AS INTEGER)
                        END
                    ) > (
                        CASE 
                            WHEN МесяцПо IS NULL OR TRIM(МесяцПо) = '' THEN CAST(МесяцС AS INTEGER)
                            ELSE CAST(МесяцПо AS INTEGER)
                        END
                    )
                    OR (
                        CASE 
                            WHEN МесяцС IS NULL OR TRIM(МесяцС) = '' THEN CAST(МесяцПо AS INTEGER)
                            ELSE CAST(МесяцС AS INTEGER)
                        END
                    ) >= ?
                    OR (
                        CASE 
                            WHEN МесяцПо IS NULL OR TRIM(МесяцПо) = '' THEN CAST(МесяцС AS INTEGER)
                            ELSE CAST(МесяцПо AS INTEGER)
                        END
                    ) <= ?
                """
                params.append(form_month_from)
                params.append(form_month_to)
            else:
                # СЛУЧАЙ 2: Интервал формы нормальный (form_from <= form_to)
                # Пересечение если:
                # - Отчет нормальный: form_from <= report_to AND form_to >= report_from
                # - Отчет циклический: form_from >= report_from OR form_to <= report_to
                query += """
                    (
                        (
                            CASE 
                                WHEN МесяцС IS NULL OR TRIM(МесяцС) = '' THEN CAST(МесяцПо AS INTEGER)
                                ELSE CAST(МесяцС AS INTEGER)
                            END
                        ) <= (
                            CASE 
                                WHEN МесяцПо IS NULL OR TRIM(МесяцПо) = '' THEN CAST(МесяцС AS INTEGER)
                                ELSE CAST(МесяцПо AS INTEGER)
                            END
                        )
                        AND (
                            CASE 
                                WHEN МесяцС IS NULL OR TRIM(МесяцС) = '' THEN CAST(МесяцПо AS INTEGER)
                                ELSE CAST(МесяцС AS INTEGER)
                            END
                        ) <= ?
                        AND (
                            CASE 
                                WHEN МесяцПо IS NULL OR TRIM(МесяцПо) = '' THEN CAST(МесяцС AS INTEGER)
                                ELSE CAST(МесяцПо AS INTEGER)
                            END
                        ) >= ?
                    )
                    OR (
                        (
                            CASE 
                                WHEN МесяцС IS NULL OR TRIM(МесяцС) = '' THEN CAST(МесяцПо AS INTEGER)
                                ELSE CAST(МесяцС AS INTEGER)
                            END
                        ) > (
                            CASE 
                                WHEN МесяцПо IS NULL OR TRIM(МесяцПо) = '' THEN CAST(МесяцС AS INTEGER)
                                ELSE CAST(МесяцПо AS INTEGER)
                            END
                        )
                        AND (
                            ? >= (
                                CASE 
                                    WHEN МесяцС IS NULL OR TRIM(МесяцС) = '' THEN CAST(МесяцПо AS INTEGER)
                                    ELSE CAST(МесяцС AS INTEGER)
                                END
                            )
                            OR ? <= (
                                CASE 
                                    WHEN МесяцПо IS NULL OR TRIM(МесяцПо) = '' THEN CAST(МесяцС AS INTEGER)
                                    ELSE CAST(МесяцПо AS INTEGER)
                                END
                            )
                        )
                    )
                """
                params.extend([form_month_to, form_month_from, form_month_from, form_month_to])
            
            query += """
                )
            """
            
            app_logger.debug(f"    Добавлен фильтр пересечения месяцев (циклический={form_is_cyclic}): форма [{form_month_from}, {form_month_to}]")
            
        except ValueError as e:
            app_logger.warning(f"    Некорректное значение месяца: МесяцС={month_from}, МесяцПо={month_to}")
    
    # Дата загрузки (фильтр "Загружено с")
    # Поле ДатаВремяЗагрузки хранится как ISO datetime строка (YYYY-MM-DDTHH:MM:SS)
    # Форма отправляет дату в формате YYYY-MM-DD от <input type="date">
    loaded_from = get_form_value(form_data, 'ЗагруженоС')
    
    if loaded_from:
        query += " AND ДатаВремяЗагрузки >= ?"
        params.append(f"{loaded_from}T00:00:00")
        app_logger.debug(f"    Добавлен фильтр: ДатаВремяЗагрузки >= {loaded_from}")
    
    return query, params
