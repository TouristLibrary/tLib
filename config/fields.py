# Version 1.0 - 21.02.2026 00:00:00 GMT
# Конфигурация полей данных TlibWebApp
# Описание: Содержит всю конфигурацию, связанную с полями данных: справочники для dropdown,
#           поля для валидации кодировки, поля для определения лёгкости запросов.
#           ВАЖНО: Синхронизировать с assets/schema.json при изменении структуры данных.
#           Поля с "description": "упорядоченный список" в schema.json должны быть в REFERENCE_FIELDS.

from .app import STATE_KATEGORIA_UNIFIED, STATE_REPORTS_COUNT


# ==================== СПРАВОЧНЫЕ ПОЛЯ (dropdown в UI) ====================

# Конфигурация загрузки справочников из БД
# Каждое поле содержит:
# - db_column: имя столбца в SQLite (должно совпадать с полем в schema.json)
# - default_prefix: значения по умолчанию в начале списка
# - api_endpoint: URL путь для API endpoint (без /api/ префикса)
# - state_key: ключ для хранения в app.state
# - use_unified: (опционально) True для объединённого списка категорий
REFERENCE_FIELDS = {
    'dopshifr': {
        'db_column': 'ДопШифр',
        'default_prefix': ["", "нет"],
        'api_endpoint': 'dopshifr-list',
        'state_key': 'dopshifr_list',
        'display_name': 'ДопШифр'
    },
    'raion_obshiy': {
        'db_column': 'РайонОбщий',
        'default_prefix': [""],
        'api_endpoint': 'raion-obshiy-list',
        'state_key': 'raion_obshiy_list',
        'display_name': 'РайонОбщий'
    },
    'tip': {
        'db_column': 'Тип',
        'default_prefix': [""],
        'api_endpoint': 'tip-list',
        'state_key': 'tip_list',
        'display_name': 'Тип'
    },
    'kategoria_s': {
        'db_column': 'КатегорияС',
        'default_prefix': [""],
        'api_endpoint': 'kategoria-s-list',
        'state_key': 'kategoria_s_list',
        'display_name': 'КатегорияС',
        'use_unified': True
    },
    'kategoria_po': {
        'db_column': 'КатегорияПо',
        'default_prefix': [""],
        'api_endpoint': 'kategoria-po-list',
        'state_key': 'kategoria_po_list',
        'display_name': 'КатегорияПо',
        'use_unified': True
    }
}


# ==================== СТРОКОВЫЕ ПОЛЯ ДЛЯ ВАЛИДАЦИИ ====================

# Поля для проверки кодировки в JSON файлах
# ВАЖНО: Должны соответствовать строковым полям в assets/schema.json
# ИмяФайла удалено из schema.json (v2.3) — имя выводится из ID и ТипФайла
STRING_FIELDS_FOR_VALIDATION = [
    "ДопШифр", "Маршрут", "РайонОбщий", "Район",
    "Автор", "Город", "Тип", "ТипСудна",
    "КатегорияС", "КатегорияПо", "Комментарии",
    "ТипФайла", "ЗагрузилИмя",
    "ДатаВремяЗагрузки"
]


# ==================== ПОЛЯ ПОИСКА ====================

# Поля для определения "лёгкого" запроса (точное совпадение)
# Запросы с любым непустым значением этих полей считаются лёгкими
SEARCH_EXACT_FIELDS = ['Шифр', 'ДопШифр', 'Автор', 'РайонОбщий']

# Поля с требованием минимальной длины для "лёгкого" запроса
# Эти поля должны быть длиннее FILTER_MIN_LENGTH для признания запроса лёгким
SEARCH_LENGTH_FIELDS = ['Маршрут', 'Район']


# ==================== HELPER ФУНКЦИИ ====================

def get_default_reference_values() -> dict:
    """
    Возвращает значения по умолчанию для всех справочных списков.

    Используется при ошибке загрузки справочников из БД.

    Returns:
        dict: Словарь с ключами state_key и значениями default_prefix
    """
    defaults = {}
    for field_config in REFERENCE_FIELDS.values():
        state_key = field_config['state_key']
        default_prefix = field_config['default_prefix']
        defaults[state_key] = default_prefix

    defaults[STATE_KATEGORIA_UNIFIED] = [""]
    defaults[STATE_REPORTS_COUNT] = 0

    return defaults


def get_db_columns() -> frozenset:
    """
    Возвращает frozenset всех имён столбцов БД из конфигурации.

    Используется для защиты от SQL injection при валидации имён столбцов.

    Returns:
        frozenset: Набор имён столбцов
    """
    return frozenset(cfg['db_column'] for cfg in REFERENCE_FIELDS.values())
