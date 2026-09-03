// Version 2.0 - 14.01.2026 08:03:13 GMT
// Описание: Модуль санитизации для защиты от DOM-XSS атак.
//           Предоставляет:
//           - escapeHtml: экранирование HTML-символов перед вставкой данных в DOM
//           - validateResourceUrl: валидация URL перед установкой в href/src атрибуты (защита от javascript:/data:/внешних URL)

import { CONSTANTS } from '../config/constants.js';

// Разрешённые URL схемы для защиты от XSS
const ALLOWED_URL_SCHEMES = ['http:', 'https:'];

// Разрешённые origins для iframe (nakarte.me)
const ALLOWED_IFRAME_ORIGINS = ['https://nakarte.me'];

/**
 * Экранирует HTML-символы в строке для безопасной вставки в DOM
 * @param {*} value - Значение для экранирования (будет приведено к строке)
 * @returns {string} Экранированная строка
 * 
 * Экранируемые символы:
 * - & → &amp;   (первым, чтобы не испортить другие entity)
 * - < → &lt;    (предотвращает открытие тегов)
 * - > → &gt;    (предотвращает закрытие тегов)
 * - " → &quot;  (предотвращает выход из атрибутов)
 * - ' → &#39;   (предотвращает выход из одинарных кавычек)
 */
export function escapeHtml(value) {
    // Обрабатываем null, undefined и другие не-строковые значения
    if (value === null || value === undefined) {
        return '';
    }
    
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

/**
 * Экранирует значение для безопасной вставки в HTML-атрибуты
 * @param {*} value - Значение для экранирования
 * @returns {string} Экранированная строка
 * 
 * Примечание: Для атрибутов требуется то же экранирование, что и для текстового контента,
 * поэтому используется та же реализация. Отдельный экспорт нужен для семантической ясности.
 */
export const escapeAttribute = escapeHtml;

/**
 * Валидирует URL для безопасного использования в href/src атрибутах.
 * 
 * Защита от XSS при компрометации источника данных:
 * - Блокирует опасные схемы: javascript:, data:, blob:, file:, vbscript:
 * - Проверяет origin: для DEFAULT разрешён только same-origin
 * - Для IFRAME_NAKARTE разрешены about:blank и https://nakarte.me
 * 
 * @param {string} url - URL для проверки
 * @param {string} context - Контекст использования: VALIDATION_CONTEXTS.DEFAULT | VALIDATION_CONTEXTS.IFRAME_NAKARTE
 * @returns {{ valid: boolean, url: string, reason?: string }} Результат валидации
 * 
 * @example
 * // Легитимный URL
 * validateResourceUrl('/api/archive/123/file/track.gpx') 
 * // => { valid: true, url: 'http://localhost/api/archive/123/file/track.gpx' }
 * 
 * @example
 * // XSS попытка
 * validateResourceUrl('javascript:alert(1)')
 * // => { valid: false, url: '', reason: 'forbidden-scheme' }
 * 
 * @example
 * // Внешний URL (блокируется для default)
 * validateResourceUrl('https://evil.com/malware.pdf')
 * // => { valid: false, url: '', reason: 'forbidden-origin' }
 * 
 * @example
 * // nakarte.me iframe (разрешён только для iframe-nakarte)
 * validateResourceUrl('https://nakarte.me/#tracks', 'iframe-nakarte')
 * // => { valid: true, url: 'https://nakarte.me/#tracks' }
 */
export function validateResourceUrl(url, context = CONSTANTS.VALIDATION_CONTEXTS.DEFAULT) {
    // Проверка базовых условий
    if (!url || typeof url !== 'string') {
        return { valid: false, url: '', reason: 'empty' };
    }

    const trimmed = url.trim();
    
    // Пустая строка после trim
    if (!trimmed) {
        return { valid: false, url: '', reason: 'empty' };
    }
    
    // Специальный случай: about:blank разрешён только для iframe
    if (context === CONSTANTS.VALIDATION_CONTEXTS.IFRAME_NAKARTE && trimmed === 'about:blank') {
        return { valid: true, url: trimmed };
    }

    try {
        // Парсим URL (относительные URL разрешаются относительно текущего origin)
        const parsed = new URL(trimmed, window.location.origin);
        
        // Блокируем опасные схемы
        // javascript: - XSS по клику
        // data: - встраивание произвольного контента
        // blob: - обход CSP
        // file: - доступ к локальным файлам
        // vbscript: - VBScript injection (IE legacy)
        const allowedSchemes = ALLOWED_URL_SCHEMES;
        if (!allowedSchemes.includes(parsed.protocol)) {
            return { valid: false, url: '', reason: 'forbidden-scheme' };
        }
        
        // Проверка origin в зависимости от контекста
        if (context === CONSTANTS.VALIDATION_CONTEXTS.IFRAME_NAKARTE) {
            // Для iframe nakarte.me разрешаем только https://nakarte.me
            if (ALLOWED_IFRAME_ORIGINS.includes(parsed.origin)) {
                return { valid: true, url: parsed.href };
            }
            return { valid: false, url: '', reason: 'forbidden-origin' };
        }
        
        // Для остальных контекстов ('default') — только same-origin
        if (parsed.origin !== window.location.origin) {
            return { valid: false, url: '', reason: 'forbidden-origin' };
        }
        
        return { valid: true, url: parsed.href };
        
    } catch (error) {
        // Невалидный URL (не парсится)
        return { valid: false, url: '', reason: 'invalid-url' };
    }
}
