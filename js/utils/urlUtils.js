// Version 1.0 - 20.02.2026
// Описание: Утилиты для работы с URL. Объединяет hashUtils (парсинг window.location.hash)
//           и shareUrl (формирование share-URL с RFC3986-совместимым кодированием).

// =============================================================================
// HASH URL
// =============================================================================

/**
 * Парсит текущий window.location.hash и возвращает URLSearchParams.
 * Возвращает null если hash пустой или содержит только '#'.
 *
 * @returns {URLSearchParams|null}
 */
export function getHashParams() {
    try {
        const raw = (window.location.hash || '').trim();
        if (!raw || raw === '#') return null;
        const qs = raw.startsWith('#') ? raw.slice(1) : raw;
        return new URLSearchParams(qs);
    } catch {
        return null;
    }
}

// =============================================================================
// SHARE URL
// =============================================================================

/**
 * Кодирует строку в RFC3986-совместимом виде.
 *
 * Заметка: encodeURIComponent близок к RFC3986, но оставляет неэкранированными символы: ! ' ( ) *
 * Поэтому мы кодируем их дополнительно, чтобы совпасть с urllib.parse.quote(..., safe="-_.~") на сервере.
 *
 * @param {string} text - исходная строка
 * @returns {string} RFC3986-совместимая строка
 */
function encodeShareQuery(text) {
    const raw = (text ?? '').toString();
    return encodeURIComponent(raw).replace(/[!'()*]/g, (ch) =>
        `%${ch.charCodeAt(0).toString(16).toUpperCase()}`
    );
}

/**
 * Возвращает базовый URL текущей страницы (origin + pathname) в формате, совместимом с текущим UI.
 * Сейчас UI исторически убирает завершающий '/', поэтому делаем так же для единообразия.
 *
 * @returns {string} baseUrl
 */
function getShareBaseUrl() {
    if (typeof window === 'undefined' || !window.location) {
        throw new Error('getShareBaseUrl: window.location недоступен (ожидается браузерное окружение)');
    }

    let base = `${window.location.origin}${window.location.pathname}`;
    if (base.endsWith('/')) base = base.slice(0, -1);
    return base;
}

/**
 * Формирует полный share-URL из уже собранной строки query (например, "123-ТССР" или "123-A&B").
 *
 * @param {string} linkText - строка query без '?'
 * @returns {string} полный URL
 */
export function buildShareUrlFromText(linkText) {
    const base = getShareBaseUrl();
    return `${base}?${encodeShareQuery(linkText)}`;
}

/**
 * Формирует полный share-URL из частей (Шифр, ДопШифр).
 *
 * @param {string} shifr - основной шифр
 * @param {string} dopShifr - дополнительный шифр (опционально)
 * @returns {string} полный URL
 */
export function buildShareUrlFromParts(shifr, dopShifr = '') {
    const sh = (shifr ?? '').toString();
    const dop = (dopShifr ?? '').toString();
    const query = dop && dop.trim() !== '' ? `${sh}-${dop}` : sh;
    return buildShareUrlFromText(query);
}
