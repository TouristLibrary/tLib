// Version 3.1 - 15.01.2026
// Главный файл констант приложения TlibWebApp
// Описание: Реэкспортирует все константы из модулей для обратной совместимости.
//           Модули разделены по категориям: api, selectors, messages, files, ui, table, form, tabs, misc.
//           MIME_TYPES удален - используется serverConfig.mimeTypes из API.

import { deepFreeze } from '../utils/freeze.js';

// Импорты из модулей
import { API, EXTERNAL_URLS, POSTMESSAGE_TYPES } from './api.config.js';
import { SELECTORS } from './ui.config.js';
import { MESSAGES } from './messages.config.js';
import { FILE_CATEGORIES, FILE_EXTENSIONS, FILE_TYPES } from './files.config.js';
import { CSS_CLASSES, BREAKPOINTS, SIZES, ICONS, VALIDATION_CONTEXTS } from './ui.config.js';
import { TABS, TAB_IDS, TAB_TOKENS } from './tabs.config.js';
import { TIMING, LIMITS, REGEX, SPECIAL_VALUES } from './misc.config.js';

/**
 * Режим отладки
 * true  - debug-логи включены (разработка, тестирование)
 * false - debug-логи отключены (production)
 */
export const DEBUG_MODE = false;

// Реэкспорт для прямого использования
export { MONTHS_RU } from './misc.config.js';
export { TABLE_HEADERS, TABLE_COLUMN_STYLES } from './table.config.js';
export { TAB_IDS, TAB_TOKENS };

/**
 * Основной объект констант (собран из всех модулей для обратной совместимости)
 */
export const CONSTANTS = deepFreeze({
    // Файлы
    FILE_CATEGORIES,
    FILE_EXTENSIONS,
    FILE_TYPES,

    // DOM
    SELECTORS,

    // Сообщения
    MESSAGES,

    // API
    API,
    EXTERNAL_URLS,
    POSTMESSAGE_TYPES,

    // Время и лимиты
    TIMING,
    LIMITS,

    // Размеры и breakpoints
    SIZES,
    BREAKPOINTS,

    // Регулярные выражения
    REGEX,

    // Специальные значения
    SPECIAL_VALUES,

    // UI
    CSS_CLASSES,
    TABS,
    ICONS,
    VALIDATION_CONTEXTS
});
