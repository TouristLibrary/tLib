// Version 5.2 - 15.06.2026 - импорт serverConfigService обновлён на js/services/
// Модуль системы редиректов и обработки URL с поддержкой истории браузера
// Описание: Обрабатывает URL параметры для автоматического поиска по шифрам.
//           Классы: URLParser (парсинг шифров), URLSerializer (сериализация формы в URL),
//           URLRedirectHandler (обработка URL, executeFormSearch, popstate, updateBrowserUrlFullFormat),
//           RedirectManager (инициализация редиректов, очистка URL).
//           URL обновляется в два этапа: pushState полный формат при начале, replaceState компактный если 1 результат.
//           Поддерживает поле ЗагруженоС для фильтрации по дате загрузки.
//           Дополнительно: поддерживает флаг ?notfound=1 (серверный legacy fallback) — показывает «Ничего не найдено»
//           без API-запроса.
//           Автопоиск не зависит от DOM формы: запрос FormData собирается напрямую из params (URL), чтобы избежать гонок
//           со справочниками и <select> опциями. UI форма заполняется отдельно и может «догнать» значения позже.
//           Декаплинг: вместо прямого вызова search.js/ui.js эмитирует события redirect:auto-search и redirect:form-reset.

import { CONSTANTS } from '../config/constants.js';
import { errorHandler } from '../core/errorHandler.js';
import { appState } from '../core/appState.js';
import { buildShareUrlFromParts } from '../utils/urlUtils.js';
import { getServerConfig } from '../services/serverConfigService.js';

// Поля формы поиска (без sort-параметров — они обрабатываются отдельно)
const FORM_FIELDS = [
    'Маршрут', 'Шифр', 'ДопШифр', 'РайонОбщий', 'Район',
    'Автор', 'Тип', 'КатегорияС', 'КатегорияПо',
    'ГодС', 'ГодПо', 'МесяцС', 'МесяцПо', 'ЗагруженоС'
];

// Значения сортировки по умолчанию — не включаются в URL
const SORT_DEFAULT_COLUMN = 'Год';
const SORT_DEFAULT_ORDER = 'desc';

export function buildFormDataFromParams(params) {
    const formData = new FormData();
    if (!params || typeof params !== 'object') return formData;

    Object.entries(params).forEach(([key, value]) => {
        const str = String(value ?? '').trim();
        if (str) {
            formData.append(key, str);
        }
    });

    return formData;
}

/**
 * Парсер URL параметров
 */
class URLParser {
    /**
     * Парсит строку запроса и извлекает шифр и дополнительный шифр
     * @param {string} queryString - Строка запроса URL
     * @returns {object|null} Объект с shifr и dopShifr или null
     */
    static parseShifrFromQuery(queryString) {
        if (!queryString) return null;

        const match = queryString.match(CONSTANTS.REGEX.SHIFR_PATTERN);
        if (match) {
            const shifr = parseInt(match[1], 10).toString();
            const dopShifr = match[2] || "";
            return { shifr, dopShifr };
        }

        return null;
    }

    /**
     * Получает строку запроса из текущего URL
     * @returns {string} Декодированная строка запроса
     */
    static getCurrentQuery() {
        const rawQuery = window.location.search.substring(1);
        return decodeURIComponent(rawQuery);
    }

}

/**
 * Сериализатор URL для работы с параметрами формы поиска
 * Поддерживает гибридный формат: компактный для простых запросов, полный для сложных
 */
class URLSerializer {
    /**
     * Конвертирует FormData в обычный объект
     * @param {FormData|object} formData - Данные формы
     * @returns {object} Объект с параметрами
     */
    static formDataToObject(formData) {
        if (formData instanceof FormData) {
            const obj = {};
            for (const [key, value] of formData.entries()) {
                obj[key] = value;
            }
            return obj;
        }
        return formData || {};
    }

    /**
     * Сериализует данные формы в URL query string
     * Всегда использует полный формат с именами полей (?Шифр=...&Тип=...)
     * Компактный формат (?123-ш) используется отдельно при одном результате поиска
     * @param {FormData|object} formData - Данные формы
     * @returns {string} Полный URL с query string
     */
    static serializeToUrl(formData) {
        const params = URLSerializer.formDataToObject(formData);
        const baseUrl = `${window.location.origin}${window.location.pathname}`;

        const searchParams = new URLSearchParams();

        FORM_FIELDS.forEach(field => {
            const value = params[field];
            if (value && String(value).trim() !== '') {
                searchParams.append(field, String(value).trim());
            }
        });

        // Добавляем sort-параметры только если отличаются от дефолтов
        const sortColumn = params['sortColumn'] ? String(params['sortColumn']).trim() : '';
        const sortOrder = params['sortOrder'] ? String(params['sortOrder']).trim() : '';
        if (sortColumn && sortColumn !== SORT_DEFAULT_COLUMN) {
            searchParams.append('sortColumn', sortColumn);
        }
        if (sortOrder && sortOrder !== SORT_DEFAULT_ORDER) {
            searchParams.append('sortOrder', sortOrder);
        }

        const queryString = searchParams.toString();
        return queryString ? `${baseUrl}?${queryString}` : baseUrl;
    }

    /**
     * Десериализует URL query string в объект параметров
     * Поддерживает оба формата: компактный и полный
     * @param {string} queryString - Строка запроса (без ?)
     * @returns {object} Объект с параметрами формы
     */
    static deserializeFromUrl(queryString) {
        if (!queryString) return {};

        const decodedQuery = decodeURIComponent(queryString);

        // Компактный формат (без =): 123 или 123-ш
        if (!queryString.includes('=')) {
            const parsed = URLParser.parseShifrFromQuery(decodedQuery);
            if (parsed) {
                const result = { 'Шифр': parsed.shifr };
                if (parsed.dopShifr) {
                    result['ДопШифр'] = parsed.dopShifr;
                }
                return result;
            }
            return {};
        }

        // Полный формат: Маршрут=...&Тип=...
        const params = new URLSearchParams(queryString);
        const result = {};

        FORM_FIELDS.forEach(field => {
            const value = params.get(field);
            if (value && value.trim() !== '') {
                result[field] = value.trim();
            }
        });

        // Восстанавливаем sort-параметры из URL (если присутствуют)
        const sortColumn = params.get('sortColumn');
        const sortOrder = params.get('sortOrder');
        if (sortColumn && sortColumn.trim()) {
            result['sortColumn'] = sortColumn.trim();
        }
        if (sortOrder && sortOrder.trim()) {
            result['sortOrder'] = sortOrder.trim();
        }

        return result;
    }

    /**
     * Проверяет, есть ли заполненные поля в параметрах
     * @param {object} params - Объект с параметрами
     * @returns {boolean} True если есть хотя бы одно заполненное поле
     */
    static hasFilledParams(params) {
        if (!params || typeof params !== 'object') return false;

        return Object.values(params).some(value => value && String(value).trim() !== '');
    }
}

/**
 * Обработчик URL редиректов
 */
export class URLRedirectHandler {
    // Статическое хранилище для отслеживания обработанных URL
    static processedUrls = new Set();

    /**
     * Обрабатывает URL параметры и выполняет автоматический поиск
     * Всегда выполняет автопоиск при загрузке URL с параметрами
     * @returns {Promise<void>} Промис завершения обработки
     */
    static async handleUrlQuery() {
        const queryString = URLParser.getCurrentQuery();
        if (!queryString) return;

        // Предотвращение циклов: пропускаем уже обработанные URL
        if (URLRedirectHandler.isProcessedUrl(queryString)) return;

        // Спец-флаг для серверного fallback: показать «Ничего не найдено» без API-запроса
        try {
            const qsParams = new URLSearchParams(queryString);
            if (qsParams.get('notfound') === '1') {
                URLRedirectHandler.markUrlAsProcessed(queryString);
                appState.setSearching(false);
                appState.setSearchResults([]);
                return;
            }
        } catch (error) {
            console.warn('handleUrlQuery: не удалось распарсить query для notfound:', error);
        }

        // 1. Компактный формат (без =): "123" или "123-ш"
        if (!queryString.includes('=')) {
            const directParse = URLParser.parseShifrFromQuery(queryString);
            if (directParse) {
                URLRedirectHandler.markUrlAsProcessed(queryString);

                const params = { 'Шифр': directParse.shifr };
                if (directParse.dopShifr) {
                    params['ДопШифр'] = directParse.dopShifr;
                } else {
                    // Компактный формат без ДопШифр — ищем только записи без ДопШифр
                    params['ДопШифр'] = getServerConfig().specialValues.noDopShifr;
                }

                await URLRedirectHandler.executeFormSearch(params);
                return;
            }
        }

        // 2. Полный формат с GET-параметрами: "Маршрут=...&Тип=..."
        if (queryString.includes('=')) {
            const params = URLSerializer.deserializeFromUrl(queryString);

            if (URLSerializer.hasFilledParams(params)) {
                URLRedirectHandler.markUrlAsProcessed(queryString);
                await URLRedirectHandler.executeFormSearch(params);
                return;
            }
        }

        // Редиректы по таблице обрабатываются на сервере через HTTP 301
    }

    /**
     * Выполняет поиск по параметрам формы
     * НЕ обновляет URL — он уже правильный
     * @param {object} params - Объект с параметрами формы
     */
    static executeFormSearch(params) {
        appState.emit('redirect:auto-search', { params });
    }

    /**
     * Обновляет URL браузера без перезагрузки страницы (для простых запросов по шифру)
     * @param {string} shifr - Основной шифр
     * @param {string} dopShifr - Дополнительный шифр (опционально)
     * @param {boolean} usePushState - Использовать pushState (true) или replaceState (false)
     */
    static updateBrowserUrl(shifr, dopShifr = '', usePushState = false) {
        const shareUrl = buildShareUrlFromParts(shifr, dopShifr);
        const stateData = { type: 'compact', shifr, dopShifr };

        try {
            if (usePushState) {
                window.history.pushState(stateData, '', shareUrl);
            } else {
                window.history.replaceState(stateData, '', shareUrl);
            }
        } catch (error) {
            console.warn('Не удалось обновить URL в браузере:', error);
        }
    }

    /**
     * Обновляет URL браузера на полный формат (pushState)
     * Используется при начале поиска до получения результатов
     * После получения результатов, если найден 1 отчёт, URL заменяется на компактный через updateBrowserUrl
     * @param {FormData|object} formData - Данные формы поиска
     */
    static updateBrowserUrlFullFormat(formData) {
        try {
            const params = URLSerializer.formDataToObject(formData);

            if (!URLSerializer.hasFilledParams(params)) return;

            const url = URLSerializer.serializeToUrl(formData);
            const stateData = { type: 'full', params };

            window.history.pushState(stateData, '', url);
        } catch (error) {
            console.warn('updateBrowserUrlFullFormat: не удалось обновить URL:', error);
        }
    }

    /**
     * Обработчик события popstate (кнопки назад/вперёд браузера)
     * @param {PopStateEvent} event - Событие popstate
     */
    static handlePopState(event) {
        // Игнорируем popstate без состояния — начальная загрузка в некоторых браузерах (Safari)
        if (!event.state) return;

        const queryString = window.location.search.substring(1);
        const stateType = event.state.type;

        // Начальное состояние или пустой URL — очищаем форму
        if (stateType === 'initial' || !queryString) {
            appState.emit('redirect:form-reset');
            return;
        }

        const params = URLSerializer.deserializeFromUrl(queryString);

        if (!URLSerializer.hasFilledParams(params)) return;

        URLRedirectHandler.executeFormSearch(params);
    }

    /**
     * Инициализирует обработчик события popstate
     */
    static initPopStateHandler() {
        window.addEventListener('popstate', URLRedirectHandler.handlePopState);
    }

    /**
     * Проверяет, был ли URL уже обработан
     * @param {string} queryString - Строка запроса
     * @returns {boolean} True если URL уже обрабатывался
     */
    static isProcessedUrl(queryString) {
        return URLRedirectHandler.processedUrls.has(queryString);
    }

    /**
     * Отмечает URL как обработанный
     * @param {string} queryString - Строка запроса
     */
    static markUrlAsProcessed(queryString) {
        URLRedirectHandler.processedUrls.add(queryString);

        // Ограничиваем размер Set для предотвращения утечек памяти
        if (URLRedirectHandler.processedUrls.size > CONSTANTS.LIMITS.MAX_PROCESSED_URLS) {
            const firstItem = URLRedirectHandler.processedUrls.values().next().value;
            URLRedirectHandler.processedUrls.delete(firstItem);
        }
    }

}

/**
 * Основной класс управления системой редиректов
 */
export class RedirectManager {
    /**
     * Инициализирует систему редиректов и обработчик истории браузера
     * @returns {Promise<void>} Промис завершения инициализации
     */
    static async initialize() {
        try {
            URLRedirectHandler.initPopStateHandler();

            // Устанавливаем начальное состояние истории для корректной работы popstate
            const initialState = { type: 'initial', timestamp: Date.now() };
            window.history.replaceState(initialState, '', window.location.href);

            await URLRedirectHandler.handleUrlQuery();
        } catch (error) {
            errorHandler.handle(error, false);
            console.error('Ошибка инициализации системы редиректов:', error);
        }
    }
}

// Экспортируем основные классы - все классы уже экспортированы напрямую
