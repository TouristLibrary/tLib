// CSS классы, breakpoints, иконки, размеры и DOM-селекторы для UI

import { deepFreeze } from '../utils/freeze.js';

/**
 * CSS классы для манипуляции через JavaScript
 */
export const CSS_CLASSES = deepFreeze({
    PLAIN_MODE: 'plain-mode',                 // режим упрощённого отображения (без header/footer)
    SIDEBAR_COLLAPSED: 'sidebar-collapsed',   // свёрнутая боковая панель
    LOADING: 'loading',                       // состояние загрузки (кнопка, изображение)
    NOT_PLACEHOLDER: 'not-placeholder',       // выбрано значение в select (не placeholder)
    ACTIVE: 'active'                          // активный элемент (таб, ссылка)
});

/**
 * Breakpoints для адаптивного дизайна (в пикселях)
 */
export const BREAKPOINTS = deepFreeze({
    DESKTOP: 900
});

/**
 * Размеры для вычислений
 */
export const SIZES = deepFreeze({
    VIEWER_BOTTOM_OFFSET_FALLBACK: 65  // px, fallback для CSS-переменной --viewer-bottom-offset-desktop
});

/**
 * SVG иконки (ID из sprite)
 */
export const ICONS = deepFreeze({
    SHARE: '#icon-share',
    DOWNLOAD: '#arrow-down-to-line',
    EXTERNAL: '#arrow-up-right',
    CHECK: '#icon-check',
    SORT_UP: '#icon-triangle-up',    // сортировка по убыванию (от больших к меньшим)
    SORT_DOWN: '#icon-triangle-down' // сортировка по возрастанию (от меньших к большим)
});

/**
 * Контексты валидации URL (используются в sanitize.js, single.js, archive.js)
 */
export const VALIDATION_CONTEXTS = deepFreeze({
    DEFAULT: 'default',
    IFRAME_NAKARTE: 'iframe-nakarte'
});

/**
 * DOM селекторы
 */
export const SELECTORS = deepFreeze({
    SEARCH_FORM: '#searchForm',
    SEARCH_BUTTON: '#searchButton',
    RESULTS: '#results',
    ERROR_MSG: '#errorMsg',
    SEARCH_FOOTER: '.search-footer',
    CLEAR_FORM_BTN: '#clearFormBtn',
    HEADER_RELOAD_LINK: '#headerReloadLink',
    SIDEBAR_COLLAPSE_BTN: '#sidebarCollapseBtn',
    SIDEBAR_EXPAND_BTN: '#sidebarExpandBtn',
    SIDEBAR_TOGGLE: '#sidebarToggle',
    REPORTS_NUMBER: '#reportsNumber',
    LOADING_INDICATOR: '#loading-indicator',
    RESULTS_TABLE: '#results-table',
    SEARCH_PANEL: '.search-panel',
    MAIN_CONTENT: '.main-content'
});
