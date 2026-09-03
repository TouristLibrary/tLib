// Version 1.0 - 20.02.2026 - Зона 7: выделен из single.js
// Описание: Generic-контроллер переключения табов.
//   Инкапсулирует DOM-механику (active class на кнопках/панелях) и предоставляет
//   программный API switchTo(tabId), заменяющий антипаттерн tabButton.click().
//   Никаких зависимостей от бизнес-логики (стратегии, hash, cache) — чистый UI-паттерн.

/**
 * Инициализирует контроллер табов: навешивает click-listeners и возвращает programmatic API.
 *
 * @param {object} config
 * @param {string} config.buttonSelector  - CSS-селектор кнопок табов (например '.tab-button')
 * @param {string} config.contentSelector - CSS-селектор панелей контента (например '.tab-content')
 * @param {string} config.activeClass     - CSS-класс активного состояния (например 'active')
 * @param {function(tabId: string, tabContent: Element|null, button: Element|null): void} config.onSwitch
 *   Callback вызывается после переключения active class. Получает:
 *     tabId      — значение data-tab на кнопке
 *     tabContent — соответствующий panel (getElementById(tabId)) или null
 *     button     — кликнутая/вызванная кнопка или null (при программном вызове без кнопки)
 * @returns {{ switchTo: function(tabId: string): void }}
 */
export function initTabs({ buttonSelector, contentSelector, activeClass, onSwitch }) {
    /**
     * Переключает активный таб (DOM + callback).
     * @param {string} tabId
     * @param {Element|null} [sourceButton] - кнопка-инициатор (null при программном вызове)
     */
    function switchTo(tabId, sourceButton = null) {
        // Снимаем active со всех кнопок
        document.querySelectorAll(buttonSelector).forEach(btn => {
            btn.classList.remove(activeClass);
        });

        // Снимаем active со всех панелей
        document.querySelectorAll(contentSelector).forEach(panel => {
            panel.classList.remove(activeClass);
        });

        // Ставим active на целевую кнопку (ищем по data-tab если не передана напрямую)
        const button = sourceButton || document.querySelector(`${buttonSelector}[data-tab="${tabId}"]`);
        if (button) button.classList.add(activeClass);

        // Ставим active на целевую панель
        const tabContent = document.getElementById(tabId);
        if (tabContent) tabContent.classList.add(activeClass);

        // Вызываем бизнес-хук
        if (typeof onSwitch === 'function') {
            onSwitch(tabId, tabContent, button);
        }
    }

    // Навешиваем click-listeners на все кнопки
    document.querySelectorAll(buttonSelector).forEach(button => {
        button.addEventListener('click', function() {
            const tabId = this.getAttribute('data-tab');
            if (tabId) switchTo(tabId, this);
        });
    });

    return { switchTo };
}
