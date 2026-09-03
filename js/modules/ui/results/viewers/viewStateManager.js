// Version 1.0 - 20.02.2026 - Зона 2: выделен из viewerHelpers.js и pdfViewer.js
// Описание: Централизованное управление hash URL state и памятью страниц PDF.
//   Единственное место для: HASH_PARAMS, parse/build/replace hash, pdfPageMemory,
//   postMessage-синхронизация страницы PDF.
//   API:
//     parseViewStateFromHash() -> {tab, file, p}
//     replaceUrlHash(state)
//     clearHash()
//     getSavedPdfPage(key) -> number|undefined
//     setupPdfPageSyncListener()

import { CONSTANTS } from '../../../../config/constants.js';
import { getHashParams } from '../../../../utils/urlUtils.js';

// =============================================================================
// HASH STATE
// =============================================================================

/** Параметры hash URL для deep-linking */
const HASH_PARAMS = { TAB: 'tab', FILE: 'file', PAGE: 'p' };

/**
 * Парсит hash URL как параметры состояния viewer'а.
 * Ожидаемый формат: #tab=pdf|geo|img|etc[&file=...][&p=...]
 * @returns {{tab?: string, file?: string, p?: number}}
 */
export function parseViewStateFromHash() {
    try {
        const params = getHashParams();
        if (!params) return {};

        const tab = params.get(HASH_PARAMS.TAB) || undefined;
        const file = params.get(HASH_PARAMS.FILE) || undefined;

        const pRaw = params.get(HASH_PARAMS.PAGE);
        const pParsed = pRaw ? parseInt(pRaw, 10) : NaN;
        const p = Number.isFinite(pParsed) && pParsed >= 1 ? pParsed : undefined;

        return { tab, file, p };
    } catch (error) {
        console.warn('parseViewStateFromHash: не удалось распарсить hash URL:', error);
        return {};
    }
}

/**
 * Собирает hash URL из состояния viewer'а.
 * @param {{tab?: string, file?: string, p?: number}} state
 * @returns {string} Строка без ведущего '#', например: "tab=pdf&file=...&p=7"
 */
function buildHashFromViewState(state) {
    const params = new URLSearchParams();
    const tab = state?.tab ? String(state.tab) : '';
    const file = state?.file ? String(state.file) : '';
    const p = typeof state?.p === 'number' ? state.p : undefined;

    if (tab) params.set(HASH_PARAMS.TAB, tab);
    if (file) params.set(HASH_PARAMS.FILE, file);
    if (tab === 'pdf' && typeof p === 'number' && Number.isFinite(p) && p >= 1) {
        params.set(HASH_PARAMS.PAGE, String(p));
    }

    return params.toString();
}

/**
 * Обновляет hash URL без перезагрузки страницы (replaceState) и с сохранением history.state.
 * @param {{tab?: string, file?: string, p?: number}} state
 */
export function replaceUrlHash(state) {
    try {
        const url = new URL(window.location.href);
        const hash = buildHashFromViewState(state);
        url.hash = hash ? `#${hash}` : '';
        window.history.replaceState(window.history.state, '', url.toString());
    } catch (error) {
        console.warn('replaceUrlHash: не удалось обновить URL:', error);
    }
}

/**
 * Очищает hash URL (переключение на таб «Маршрут» или сброс состояния).
 */
export function clearHash() {
    replaceUrlHash({});
}

// =============================================================================
// PDF PAGE MEMORY
// =============================================================================

/** Хранилище номеров страниц для каждого PDF (ключ = URL файла или png-директория) */
const pdfPageMemory = new Map();

/**
 * Возвращает сохранённый номер страницы для данного PDF/директории.
 * @param {string} key - URL файла или путь к png-директории
 * @returns {number|undefined}
 */
export function getSavedPdfPage(key) {
    return pdfPageMemory.get(key);
}

/**
 * Настраивает слушатель postMessage от PNG viewer для синхронизации номера страницы в URL.
 * Не добавляет p=1 если его не было изначально (чтобы не «загрязнять» URL при первом открытии).
 * Обрабатывает событие 'pngviewer-page-change' от png-viewer iframe.
 */
export function setupPdfPageSyncListener() {
    if (setupPdfPageSyncListener._installed) return;
    setupPdfPageSyncListener._installed = true;

    window.addEventListener('message', (event) => {
        // SECURITY: проверяем origin
        if (event.origin !== window.location.origin) return;

        if (event.data?.type !== CONSTANTS.POSTMESSAGE_TYPES.PAGE_CHANGE) return;

        const pageNumber = event.data.pageNumber;
        const fileUrl = event.data.fileUrl || '';
        const pngDir = event.data.directory || '';

        if (typeof pageNumber !== 'number' || !Number.isFinite(pageNumber) || pageNumber < 1) return;

        if (fileUrl) {
            pdfPageMemory.set(fileUrl, pageNumber);
        } else if (pngDir) {
            pdfPageMemory.set(pngDir, pageNumber);
        }

        const current = parseViewStateFromHash();

        // Не добавляем p=1 если его не было изначально
        if (pageNumber === 1 && current.p === undefined) return;
        if (current.p === pageNumber) return;

        if (current.tab === 'pdf' || !current.tab) {
            replaceUrlHash({ ...current, tab: current.tab || 'pdf', p: pageNumber });
        }
    });
}
