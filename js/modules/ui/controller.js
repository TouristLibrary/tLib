// Version 1.5 - 20.02.2026
// Описание: Основной контроллер UI. Инициализирует форму/clipboard/sidebars, подписывается на hashchange для deep-link viewer,
//           синхронизирует состояние кнопки поиска и предоставляет утилиты для заполнения <select> элементами справочников.
//           Подписывается на reference:lists-loaded для заполнения <select> справочниками (декаплинг от referenceListsService).

import { CONSTANTS } from '../../config/constants.js';
import { appState } from '../../core/appState.js';
import { ButtonManager } from './form.js';
import { FormManager } from './form.js';
import { ResultsRenderer } from './results/index.js';
import { getHashParams } from '../../utils/urlUtils.js';

// ID select элементов формы поиска
const SELECT_IDS = {
    DOP_SHIFR: 'dopShifrSelect',
    RAION_OBSHIY: 'raionObshiySelect',
    TIP: 'tipSelect',
    KATEGORIA_S: 'kategoriaSSelect',
    KATEGORIA_PO: 'kategoriaPoSelect'
};

// Подписи для select элементов (placeholder)
const SELECT_LABELS = {
    DOP_SHIFR: 'ДопШифр',
    RAION_OBSHIY: 'Общий район',
    TIP: 'Тип',
    KATEGORIA_S: 'Категория с',
    KATEGORIA_PO: 'Категория по'
};

function copyToClipboard(url) {
    if (!navigator.clipboard) {
        alert(CONSTANTS.MESSAGES.CLIPBOARD_NOT_SUPPORTED);
        return;
    }
    navigator.clipboard.writeText(url)
        .then(() => {
            console.log('Ссылка скопирована:', url);
        })
        .catch(error => {
            console.error('Ошибка копирования ссылки:', error);
            alert(CONSTANTS.MESSAGES.COPY_FAILED);
        });
}

/**
 * Включает plain-mode один раз при старте приложения.
 * Сценарий A: plain=1 добавляется только в ссылках «стрелок» и живёт в hash URL.
 * После инициализации UI может переписать hash и убрать plain=1 из адресной строки — это нормально,
 * т.к. режим уже включён CSS-классом на <html>.
 */
function applyPlainModeFromHashOnce() {
    try {
        const params = getHashParams();
        if (!params) return;

        if (params.get('plain') === '1') {
            document.documentElement.classList.add(CONSTANTS.CSS_CLASSES.PLAIN_MODE);
        }
    } catch (error) {
        console.warn('applyPlainModeFromHashOnce: не удалось применить plain-mode из hash:', error);
    }
}

/**
 * Основной контроллер пользовательского интерфейса
 */
export class UIController {
    /**
     * Инициализирует все компоненты UI
     */
    static initialize() {
        // Важно: применяем plain-mode ДО любой логики, которая может переписать hash URL.
        applyPlainModeFromHashOnce();

        FormManager.initialize();
        UIController.setupHeaderReload();
        UIController.setupSidebarToggle();

        // Подписываемся на загрузку справочников (эмитирует referenceListsService)
        appState.on('reference:lists-loaded', ({ lists }) => {
            UIController.populateSelect(SELECT_IDS.DOP_SHIFR, SELECT_LABELS.DOP_SHIFR, lists.dopshifr, {
                displayText: (value, label) => value === '' ? label : value
            });
            UIController.populateSelect(SELECT_IDS.RAION_OBSHIY, SELECT_LABELS.RAION_OBSHIY, lists.raionObshiy);
            UIController.populateSelect(SELECT_IDS.TIP, SELECT_LABELS.TIP, lists.tip);
            UIController.populateSelect(SELECT_IDS.KATEGORIA_S, SELECT_LABELS.KATEGORIA_S, lists.kategoriaUnified);
            UIController.populateSelect(SELECT_IDS.KATEGORIA_PO, SELECT_LABELS.KATEGORIA_PO, lists.kategoriaUnified);
        });

        // Реагируем на ручное изменение hash URL (deep-link): #tab=pdf&file=...&p=...
        // Важно: applyViewStateFromHash() может канонизировать URL через history.replaceState,
        // но это не генерирует hashchange, поэтому циклов быть не должно.
        if (!UIController._hashChangeHandlerInstalled) {
            UIController._hashChangeHandlerInstalled = true;
            window.addEventListener('hashchange', () => {
                try {
                    // Даём браузеру закончить обработку смены hash, затем применяем состояние.
                    requestAnimationFrame(() => ResultsRenderer.applyViewStateFromHash());
                } catch (error) {
                    console.warn('hashchange: не удалось применить состояние из hash:', error);
                }
            });
        }

        // Обработчик копирования ссылки на отчет
        document.addEventListener('click', (e) => {
            const copyLink = e.target.closest('.copy-report-link');
            if (copyLink) {
                e.preventDefault();
                const shareUrl = copyLink.dataset.shareUrl;
                if (shareUrl) {
                    copyToClipboard(shareUrl);
                    
                    // Визуальная обратная связь: меняем иконку на галочку на 3 секунды
                    const useElement = copyLink.querySelector('use');
                    if (useElement) {
                        useElement.setAttribute('href', CONSTANTS.ICONS.CHECK);
                        setTimeout(() => {
                            useElement.setAttribute('href', CONSTANTS.ICONS.SHARE);
                        }, CONSTANTS.TIMING.ICON_FEEDBACK_DELAY);
                    }
                }
            }
        });
    }

    /**
     * Настраивает обработчик перезагрузки страницы при клике на заголовок
     */
    static setupHeaderReload() {
        const headerLink = document.querySelector(CONSTANTS.SELECTORS.HEADER_RELOAD_LINK);
        if (headerLink) {
            headerLink.addEventListener('click', (e) => {
                e.preventDefault();
                // Полная перезагрузка с очисткой кэша (cache-busting через timestamp)
                window.location.href = window.location.origin + window.location.pathname + '?' + CONSTANTS.SPECIAL_VALUES.CACHE_BUST_PARAM + '=' + Date.now();
            });
        }
    }

    /**
     * Настраивает переключение сворачивания боковой панели (только десктоп)
     * Кнопка сворачивания появляется в правом верхнем углу при наведении
     * В свернутом состоянии клик по логотипу не разворачивает панель (логотип ведёт на about.html)
     * При переходе на мобильный режим панель автоматически разворачивается
     */
    static setupSidebarToggle() {
        const collapseBtn = document.querySelector(CONSTANTS.SELECTORS.SIDEBAR_COLLAPSE_BTN);
        const expandBtn = document.querySelector(CONSTANTS.SELECTORS.SIDEBAR_EXPAND_BTN);
        const searchPanel = document.querySelector(CONSTANTS.SELECTORS.SEARCH_PANEL);
        const mainContent = document.querySelector(CONSTANTS.SELECTORS.MAIN_CONTENT);
        
        if (!collapseBtn || !expandBtn || !searchPanel || !mainContent) {
            console.warn('Элементы для переключения sidebar не найдены');
            return;
        }

        const isDesktopCollapsed = () =>
            window.innerWidth > CONSTANTS.BREAKPOINTS.DESKTOP &&
            mainContent.classList.contains(CONSTANTS.CSS_CLASSES.SIDEBAR_COLLAPSED);

        const expandSidebar = (label) => {
            mainContent.classList.remove(CONSTANTS.CSS_CLASSES.SIDEBAR_COLLAPSED);
            console.log(label);
        };
        
        // Обработчик клика на кнопку сворачивания (появляется при наведении)
        collapseBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (window.innerWidth > CONSTANTS.BREAKPOINTS.DESKTOP) {
                mainContent.classList.add(CONSTANTS.CSS_CLASSES.SIDEBAR_COLLAPSED);
                console.log('Sidebar collapsed');
            }
        });

        // Обработчик клика на кнопку разворачивания (появляется при наведении в свернутом состоянии)
        expandBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (isDesktopCollapsed()) expandSidebar('Sidebar expanded');
        });

        // В свернутом состоянии разворачиваем по клику в любом месте панели
        searchPanel.addEventListener('click', (e) => {
            // Не обрабатываем клики по кнопкам управления (на всякий случай)
            if (e.target?.closest?.('#sidebarCollapseBtn')) return;
            if (e.target?.closest?.('#sidebarExpandBtn')) return;
            // Логотип — это ссылка на about.html, не должен разворачивать панель
            if (e.target?.closest?.(CONSTANTS.SELECTORS.SIDEBAR_TOGGLE)) return;
            if (isDesktopCollapsed()) expandSidebar('Sidebar expanded (panel click)');
        });
        
        // Следим за изменением размера окна
        // При переходе на мобильный убираем класс свернутости
        window.addEventListener('resize', () => {
            if (window.innerWidth <= CONSTANTS.BREAKPOINTS.DESKTOP &&
                mainContent.classList.contains(CONSTANTS.CSS_CLASSES.SIDEBAR_COLLAPSED)) {
                expandSidebar('Sidebar expanded (mobile mode)');
            }
        });
    }

    /**
     * Заполняет <select> значениями справочника.
     * UIController намеренно не выполняет fetch: сеть/кэш/ошибки должны жить в сервисе данных.
     *
     * @param {string} selectId - ID элемента select
     * @param {string} label - подпись для пустого значения (value === '')
     * @param {Array} values - массив значений
     * @param {object} [options]
     * @param {(value: string, label: string) => string} [options.displayText] - функция для отображаемого текста
     * @param {boolean} [options.preserveSelection] - сохранять ли текущий выбор при обновлении списка
     */
    static populateSelect(selectId, label, values, options = {}) {
        const selectElement = document.getElementById(selectId);
        
        if (!selectElement) {
            console.warn(`UIController.populateSelect: элемент ${selectId} не найден`);
            return;
        }
        
        const preserveSelection = options.preserveSelection !== false;
        const pendingValue = preserveSelection ? (selectElement.dataset?.pendingValue ?? '') : '';
        const previousValue = preserveSelection
            ? String(pendingValue || selectElement.value || '')
            : '';
        const displayText = typeof options.displayText === 'function'
            ? options.displayText
            : (value, fallbackLabel) => (value === '' ? fallbackLabel : value);

        const rawValues = Array.isArray(values) ? values : [];
        const normalizedValues = rawValues.map(v => (v === null || v === undefined) ? '' : String(v));

        // Очищаем текущие опции
        selectElement.innerHTML = '';

        if (normalizedValues.length === 0) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = label;
            selectElement.appendChild(option);
        } else {
            normalizedValues.forEach(value => {
                const option = document.createElement('option');
                option.value = value;
                option.textContent = displayText(value, label);
                selectElement.appendChild(option);
            });
        }

        if (preserveSelection) {
            const hasPrev = normalizedValues.includes(previousValue);
            selectElement.value = hasPrev ? previousValue : '';

            // Если применили отложенное значение — очищаем маркер.
            if (hasPrev && pendingValue && selectElement.dataset?.pendingValue) {
                delete selectElement.dataset.pendingValue;
            }
        }

        FormManager.updateSelectColor(selectElement);
    }

    /**
     * Обновляет состояние поиска в UI
     * @param {boolean} isSearching - Флаг активного поиска
     */
    static updateSearchState(isSearching) {
        // При начале поиска устанавливаем состояние 'searching'
        // При окончании поиска состояние устанавливается в обработчике search:results-changed
        if (isSearching) {
            ButtonManager.setSearchState('searching');
        }
    }
}



