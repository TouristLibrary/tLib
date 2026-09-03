// Version 1.2 - 20.02.2026 - ButtonManager перенесён из buttons.js
// Описание: Управляет формой поиска: очистка/восстановление полей, оформление select, поля даты, очистка результатов и триггер поиска.
//           Поддерживает отложенную установку значений для <select> (pendingValue), когда опции подгружаются позже.
//           Содержит ButtonManager — управление состояниями кнопки поиска (waiting/ready/searching/complete).

import { CONSTANTS } from '../../config/constants.js';
import { errorHandler } from '../../core/errorHandler.js';
import { appState } from '../../core/appState.js';
import { DOMUtils } from './utils.js';

/**
 * Менеджер состояния кнопок
 */
export class ButtonManager {
    /**
     * Устанавливает состояние кнопки поиска
     * Единая функция для управления всеми состояниями кнопки
     * @param {'waiting'|'ready'|'searching'|'complete'} state - Состояние кнопки
     * @param {number} count - Количество найденных результатов (для состояния 'complete')
     */
    static setSearchState(state, count = 0) {
        const button = DOMUtils.getElement(CONSTANTS.SELECTORS.SEARCH_BUTTON);
        if (!button) {
            console.warn('Кнопка поиска не найдена');
            return;
        }

        const states = {
            'waiting':   { disabled: true,  loading: false, text: CONSTANTS.MESSAGES.WAITING },
            'ready':     { disabled: false, loading: false, text: CONSTANTS.MESSAGES.SEARCH },
            'searching': { disabled: true,  loading: true,  text: CONSTANTS.MESSAGES.SEARCHING },
            'complete':  { disabled: false, loading: false,
                           text: count === 1 ? CONSTANTS.MESSAGES.SEARCH
                                             : `${CONSTANTS.MESSAGES.SEARCH} (найдено ${count})` },
        };

        const s = states[state];
        if (!s) {
            console.warn(`Неизвестное состояние кнопки: ${state}`);
            return;
        }

        button.disabled = s.disabled;
        button.classList.toggle(CONSTANTS.CSS_CLASSES.LOADING, s.loading);
        button.textContent = s.text;
    }
}

/**
 * Менеджер форм
 */
export class FormManager {
    /**
     * Инициализирует обработчики форм
     */
    static initialize() {
        FormManager.setupClearButton();
        FormManager.setupSelectColorUpdates();
        FormManager.setupDateInputs();
    }

    /**
     * Сбрасывает форму, результаты и состояние кнопки в исходное состояние.
     * Используется как кнопкой очистки, так и при навигации по истории браузера.
     */
    static resetToInitialState() {
        const form = DOMUtils.getElement(CONSTANTS.SELECTORS.SEARCH_FORM);
        if (form) {
            FormManager.clearForm(form);
        }
        FormManager.clearResults();
        ButtonManager.setSearchState('ready');
    }

    /**
     * Настраивает кнопку очистки формы
     */
    static setupClearButton() {
        const clearBtn = DOMUtils.getElement(CONSTANTS.SELECTORS.CLEAR_FORM_BTN);
        
        if (clearBtn) {
            clearBtn.addEventListener('click', () => {
                try {
                    console.log('Нажата кнопка очистки формы');
                    FormManager.resetToInitialState();
                    // URL не очищается, чтобы не создавать пустые записи в истории
                    // URL обновится автоматически при следующем поиске
                    console.log('Форма очищена успешно');
                } catch (error) {
                    console.error('Ошибка очистки формы:', error);
                    errorHandler.handle(`Ошибка очистки формы: ${error.message}`);
                }
            });
        } else {
            console.error('Не найдены элементы:', { clearBtn: !!clearBtn });
        }
    }

    /**
     * Очищает форму поиска
     * @param {Element} form - Элемент формы
     */
    static clearForm(form) {
        form.reset();
        
        Array.from(form.elements).forEach(element => {
            if (element.tagName === "SELECT" || element.type === "text" || element.type === "number") {
                element.value = "";
            }
            if (element.type === "checkbox" || element.type === "radio") {
                element.checked = false;
            }
            // Очистка полей даты и сброс типа на text для показа placeholder
            if (element.type === "date") {
                element.value = "";
                element.type = "text";
            }
            // Дополнительная очистка для select элементов
            if (element.tagName === "SELECT") {
                element.selectedIndex = 0; // Принудительно выбираем первый option (обычно пустой)
                element.classList.remove(CONSTANTS.CSS_CLASSES.NOT_PLACEHOLDER); // Принудительно удаляем класс
            }
        });

        // Восстанавливаем сортировку к значениям по умолчанию
        // (form.reset() вернёт sortColumn к selected-опции "Год", но sortOrder и иконку нужно обновить явно)
        const sortSelect = form.querySelector('#sortSelect');
        if (sortSelect) sortSelect.value = '';
        const sortOrderInput = form.querySelector('#sortOrderInput');
        if (sortOrderInput) sortOrderInput.value = 'desc';
        FormManager._updateSortDirectionIcon('desc');

        // Обновляем цвета select элементов с небольшой задержкой для синхронизации с DOM
        // НЕ сбрасываем selectedIndex здесь — это уже сделано синхронно выше,
        // а повторный сброс конфликтует с restoreFormFromParams
        FormManager._deferredUpdateSelectColors(form);
    }

    /**
     * Очищает результаты поиска в UI и сбрасывает состояние результатов
     */
    static clearResults() {
        try {
            // Очищаем контейнер результатов
            DOMUtils.setElementHTML(CONSTANTS.SELECTORS.RESULTS, '');
            // Сбрасываем результаты в состоянии приложения (тихо, без событий)
            if (typeof appState.clearSearchResults === 'function') {
                appState.clearSearchResults();
            } else {
                // Запасной вариант для совместимости, может отрисовать "Ничего не найдено"
                appState.setSearchResults([]);
            }
            console.log('Результаты поиска очищены.');
        } catch (error) {
            console.error('Ошибка при очистке результатов поиска:', error);
            errorHandler.handle(`Ошибка очистки результатов: ${error.message}`);
        }
    }

    /**
     * Настраивает поля даты с переключением типа и календарём
     */
    static setupDateInputs() {
        const dateInputs = document.querySelectorAll('input[name="ЗагруженоС"]');
        
        dateInputs.forEach(input => {
            input.addEventListener('click', function() {
                this.type = 'date';
                this.showPicker?.();
            });
            input.addEventListener('blur', function() {
                if (!this.value) this.type = 'text';
            });
        });
        
        console.log(`Инициализировано ${dateInputs.length} полей даты`);
    }

    /**
     * Настраивает обновление цветов select элементов
     */
    static setupSelectColorUpdates() {
        // Ждем, пока DOM полностью загрузится
        setTimeout(() => {
            const selects = document.querySelectorAll('select');
            console.log(`Инициализация обработчиков цветов для ${selects.length} select элементов`);
            selects.forEach((select, index) => {
                // Добавляем обработчик изменения
                select.addEventListener('change', () => FormManager.updateSelectColor(select));
                // Устанавливаем начальный цвет
                FormManager.updateSelectColor(select);
                console.log(`Select ${index} инициализирован: value="${select.value}", hasClass=${select.classList.contains(CONSTANTS.CSS_CLASSES.NOT_PLACEHOLDER)}`);
            });
        }, 0);
    }

    /**
     * Обновляет SVG-иконку кнопки направления сортировки (#sortDirectionBtn)
     * @param {string} order - 'desc' или 'asc'
     */
    static _updateSortDirectionIcon(order) {
        const sortBtn = document.querySelector('#sortDirectionBtn');
        if (!sortBtn) return;
        const icon = sortBtn.querySelector('use');
        if (icon) {
            icon.setAttribute('href', order === 'asc' ? CONSTANTS.ICONS.SORT_DOWN : CONSTANTS.ICONS.SORT_UP);
        }
    }

    /**
     * Откладывает обновление цветов всех select элементов формы через setTimeout(0).
     * Используется после синхронных изменений значений, чтобы дождаться обновления DOM.
     * @param {Element} form - Элемент формы
     */
    static _deferredUpdateSelectColors(form) {
        setTimeout(() => {
            form.querySelectorAll('select').forEach(select => FormManager.updateSelectColor(select));
        }, 0);
    }

    /**
     * Обновляет цвет select элемента в зависимости от выбранного значения
     * @param {Element} select - Select элемент
     */
    static updateSelectColor(select) {
        try {
            // Принудительно удаляем класс, затем добавляем только если есть значение
            const wasPlaceholder = !select.classList.contains(CONSTANTS.CSS_CLASSES.NOT_PLACEHOLDER);
            select.classList.remove(CONSTANTS.CSS_CLASSES.NOT_PLACEHOLDER);
            if (select.value !== "") {
                select.classList.add(CONSTANTS.CSS_CLASSES.NOT_PLACEHOLDER);
            }
            const isPlaceholder = !select.classList.contains(CONSTANTS.CSS_CLASSES.NOT_PLACEHOLDER);
            
            // Отладочное сообщение при изменении состояния
            if (wasPlaceholder !== isPlaceholder) {
                console.log(`Select color changed: value="${select.value}", placeholder=${isPlaceholder}`);
            }
        } catch (error) {
            console.error('Ошибка при обновлении цвета select:', error);
        }
    }

    /**
     * Восстанавливает состояние формы из объекта параметров (для навигации по истории)
     * @param {object} params - Объект с параметрами формы {Шифр: '123', Тип: 'водный', ...}
     */
    static restoreFormFromParams(params) {
        const form = DOMUtils.getElement(CONSTANTS.SELECTORS.SEARCH_FORM);
        if (!form) {
            console.error('restoreFormFromParams: форма поиска не найдена');
            return;
        }

        console.log('restoreFormFromParams: восстановление формы из параметров', params);

        // Сначала очищаем форму
        FormManager.clearForm(form);

        // Заполняем поля из params
        if (params && typeof params === 'object') {
            Object.entries(params).forEach(([key, value]) => {
                const element = form.elements[key];
                if (element && value) {
                    const strValue = String(value);
                    element.value = strValue;
                    console.log(`restoreFormFromParams: установлено ${key}="${value}"`);
                    
                    // Обновляем цвет для select элементов
                    if (element.tagName === 'SELECT') {
                        // Если нужной опции ещё нет (справочник подгружается позже), запоминаем значение.
                        if (element.value !== strValue) {
                            element.dataset.pendingValue = strValue;
                        } else if (element.dataset?.pendingValue) {
                            delete element.dataset.pendingValue;
                        }
                        FormManager.updateSelectColor(element);
                    }
                }
            });
        }

        // Обновляем иконку направления сортировки по восстановленному sortOrder
        const sortOrderInput = form.querySelector('#sortOrderInput');
        if (sortOrderInput) {
            FormManager._updateSortDirectionIcon(sortOrderInput.value);
        }

        // Обновляем цвета всех select элементов с небольшой задержкой
        FormManager._deferredUpdateSelectColors(form);

        console.log('restoreFormFromParams: форма восстановлена');
    }
}

