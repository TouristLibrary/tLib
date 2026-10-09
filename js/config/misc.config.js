// Version 1.1 - 09.10.2026 08:50:00 GMT
// Различные константы: таймауты, лимиты, регулярные выражения, месяцы
// 1.1: LIMITS.SEARCH_YEAR_MIN — нижняя граница полей «Год с/по» (верхняя считается в form.js).

import { deepFreeze } from '../utils/freeze.js';

/**
 * Таймауты (в миллисекундах)
 */
export const TIMING = deepFreeze({
    ICON_FEEDBACK_DELAY: 3000,
    REFERENCE_CHECK_THROTTLE: 5 * 60 * 1000,  // 5 минут
    REFERENCE_CHECK_INTERVAL: 15 * 60 * 1000, // 15 минут
    REFERENCE_RETRY_DELAY: 800,
    // Polling кеша (resolveAndWait в single.js)
    RESOLVE_INITIAL_DELAY: 1000,       // начальная задержка между попытками (мс)
    RESOLVE_MAX_DELAY: 5000,           // максимальная задержка после экспоненциального backoff (мс)
    RESOLVE_BACKOFF_MULTIPLIER: 1.5,   // множитель для экспоненциального backoff
    // Загрузка таба
    TAB_LOAD_FALLBACK_TIMEOUT: 15000   // максимальное время ожидания загрузки контента таба (мс)
});

/**
 * Лимиты и размеры
 */
export const LIMITS = deepFreeze({
    MAX_PROCESSED_URLS: 100,
    SHIFR_PAD_LENGTH: 5,
    SEARCH_PAGE_SIZE: 100,     // количество записей на страницу при пагинации поиска
    SEARCH_PREFETCH_PX: 400,   // пикселей до конца таблицы, при которых начинается загрузка следующего чанка
    RESOLVE_MAX_ATTEMPTS: 180, // максимум попыток polling кеша (180 × 1000 мс = 3 минуты)
    // Нижняя граница полей «Год с/по»: отчётов старше в библиотеке нет, а опечатка вроде 20236
    // иначе уходит на сервер и возвращает «Ничего не найдено». Верхняя — текущий год + 1,
    // считается в FormManager.setupDateInputs
    SEARCH_YEAR_MIN: 1900
});

/**
 * Регулярные выражения
 */
export const REGEX = deepFreeze({
    SHIFR_PATTERN: /^(\d+)(?:-(.+))?$/,
    DIGITS_ONLY: /^\d+$/,
    SAFE_ENCODE_CHARS: /[^a-zA-Z0-9а-яА-ЯёЁ\-_\.]/g
});

/**
 * Специальные значения
 * Примечание: NO_DOP_SHIFR удален - используется serverConfig.specialValues.noDopShifr
 */
export const SPECIAL_VALUES = deepFreeze({
    CACHE_BUST_PARAM: '_'       // параметр для сброса кэша
});

/**
 * Месяцы на русском
 */
export const MONTHS_RU = deepFreeze([
    "янв", "фев", "мар", "апр", "май", "июн", 
    "июл", "авг", "сен", "окт", "ноя", "дек"
]);
