// Version 1.2 - 20.02.2026
// Описание: Вспомогательные утилиты UI. Содержит DOMUtils — безопасный доступ к DOM
//           с кешированием элементов (модульный кеш, без зависимости от appState).

const elementCache = {};

/**
 * Утилиты для работы с DOM
 */
export class DOMUtils {
    /**
     * Безопасное получение элемента DOM с кэшированием
     * @param {string} selector - CSS селектор
     * @param {string} cacheKey - Ключ для кэширования (опционально)
     * @returns {Element|null} DOM элемент или null
     */
    static getElement(selector, cacheKey = null) {
        const key = cacheKey || selector;
        if (elementCache[key]) return elementCache[key];
        const element = document.querySelector(selector);
        if (element && cacheKey) elementCache[key] = element;
        return element;
    }

    /**
     * Устанавливает innerHTML элемента (без санитизации).
     * ВНИМАНИЕ: Вызывающий код отвечает за безопасность данных.
     * Для пользовательских данных используйте escapeHtml() из utils/sanitize.js
     * @param {string} selector - CSS селектор
     * @param {string} html - HTML для установки
     */
    static setElementHTML(selector, html) {
        const element = DOMUtils.getElement(selector);
        if (element) {
            element.innerHTML = html;
        } else {
            console.warn(`Элемент не найден: ${selector}`);
        }
    }
}
