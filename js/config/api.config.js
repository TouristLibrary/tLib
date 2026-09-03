// Version 1.1 - 15.06.2026
// API endpoints, внешние URL и настройки безопасности

import { deepFreeze } from '../utils/freeze.js';

/**
 * API endpoints
 */
export const API = deepFreeze({
    SEARCH: '/api/search',
    REPORTS_COUNT: '/api/reports-count',
    REFERENCE_VERSION: '/api/reference-version',
    DOPSHIFR_LIST: '/api/dopshifr-list',
    RAION_OBSHIY_LIST: '/api/raion-obshiy-list',
    TIP_LIST: '/api/tip-list',
    KATEGORIA_LIST: '/api/kategoria-s-list',
    ARCHIVE_BASE: '/api/archive',
    CONFIG: '/api/config',
    CACHE_BASE: '/api/cache',
    PNG_BASE: '/api/png',
    PNG_VIEWER: '/png-viewer',
    AUTH_ME: '/api/auth/me',
    AUTH_REQUEST_LINK: '/api/auth/request-link',
    AUTH_VERIFY_CODE: '/api/auth/verify-code',
    AUTH_LOGOUT: '/api/auth/logout',
});

/**
 * Внешние URL
 */
export const EXTERNAL_URLS = deepFreeze({
    NAKARTE_BASE: 'https://nakarte.me/',
    NAKARTE_TRACK_PREFIX: 'https://nakarte.me/#nktu=',
    NAKARTE_IFRAME_PARAMS: '&min=1/1/1/1'   // параметры минимизации для iframe
});

/**
 * Типы postMessage-сообщений между single.js и png-viewer.js
 */
export const POSTMESSAGE_TYPES = deepFreeze({
    PAGE_CHANGE: 'pngviewer-page-change',
    GOTO_PAGE: 'pngviewer-goto-page'
});
