// Вкладки единственного результата (deep-link)

import { deepFreeze } from '../utils/freeze.js';

/**
 * Именованные ID вкладок (для использования вместо магических строк)
 */
export const TAB_IDS = deepFreeze({
    ROUTE: 'tab-0',
    PDF:   'tab-1',
    GEO:   'tab-2',
    IMG:   'tab-3',
    ETC:   'tab-4'
});

/**
 * Именованные токены вкладок (для deep-link URL и postMessage)
 */
export const TAB_TOKENS = deepFreeze({
    ROUTE: 'route',
    PDF:   'pdf',
    GEO:   'geo',
    IMG:   'img',
    ETC:   'etc'
});

/**
 * Маппинг ID таба на токен
 */
const TOKEN_BY_ID = {
    [TAB_IDS.ROUTE]: TAB_TOKENS.ROUTE,
    [TAB_IDS.PDF]:   TAB_TOKENS.PDF,
    [TAB_IDS.GEO]:   TAB_TOKENS.GEO,
    [TAB_IDS.IMG]:   TAB_TOKENS.IMG,
    [TAB_IDS.ETC]:   TAB_TOKENS.ETC
};

/**
 * Маппинг токена на ID таба (генерируется автоматически)
 */
const ID_BY_TOKEN = Object.fromEntries(
    Object.entries(TOKEN_BY_ID).map(([k, v]) => [v, k])
);

/**
 * Вкладки единственного результата
 */
export const TABS = deepFreeze({
    TOKEN_BY_ID,
    ID_BY_TOKEN,
    CATEGORY_NAMES: ['Маршрут', 'PDF', 'Треки', 'Изображения', 'Прочее']
});
