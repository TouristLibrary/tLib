// Описание: Объединяет рендер результатов поиска. Выбирает режим таблицы или карточки одного результата,
//           и проксирует методы таблицы/карточки, сохраняя публичный API ResultsRenderer.
//           Пустая выдача перечисляет заполненные поля поиска: иначе не видно, какое условие обнулило результат.

import { CONSTANTS, MONTHS_RU, TAB_IDS } from '../../../config/constants.js';
import { getServerConfig } from '../../../services/serverConfigService.js';
import { DOMUtils } from '../utils.js';
import { TableResultsRenderer } from './table.js';
import { SingleResultsRenderer } from './single.js';

export class ResultsRenderer {
    /**
     * Показывает вместо результатов сообщение — разметка пустой выдачи.
     * Текст ставится через textContent: разметка из текста не исполняется.
     * lines — дополнительные строки под основной (условия поиска и подсказка)
     * @param {string} text - Текст сообщения
     * @param {string[]} [lines] - Строки под основным текстом
     */
    static renderMessage(text, lines = []) {
        const resultsDiv = DOMUtils.getElement(CONSTANTS.SELECTORS.RESULTS);
        if (!resultsDiv) {
            console.error('Элемент результатов не найден');
            return;
        }
        resultsDiv.innerHTML = '<div class="single-result-formatted"></div>';
        const box = resultsDiv.querySelector('.single-result-formatted');
        for (const line of [text, ...lines]) {
            const field = document.createElement('div');
            field.className = 'single-result-field';
            const span = document.createElement('span');
            span.textContent = line;
            field.appendChild(span);
            box.appendChild(field);
        }
    }

    /**
     * Строки под «Ничего не найдено»: заполненные поля формы в её порядке.
     * Пустая форма и отсутствие formData (очистка результатов) строк не дают.
     * Подсказка убрать часть условий — только когда полей два или больше
     * @param {FormData|null} formData - форма, ушедшая в поиск
     * @returns {string[]}
     */
    static _describeConditions(formData) {
        if (!formData || typeof formData.get !== 'function') return [];

        const labels = CONSTANTS.MESSAGES.SEARCH_FIELD_LABELS;
        const noDop = getServerConfig().specialValues.noDopShifr;
        const maxLen = CONSTANTS.LIMITS.NO_RESULTS_VALUE_MAX_LENGTH;
        const parts = [];

        for (const field of Object.keys(labels)) {
            const value = String(formData.get(field) ?? '').trim();
            if (!value) continue;

            if (field === 'ДопШифр' && value === noDop) {
                parts.push(CONSTANTS.MESSAGES.DOP_SHIFR_NONE_LABEL);
                continue;
            }

            let shown = value;
            if (field === 'МесяцС' || field === 'МесяцПо') {
                shown = MONTHS_RU[Number(value) - 1] || value;
            } else if (value.length > maxLen) {
                shown = `${value.slice(0, maxLen)}…`;
            }
            parts.push(`${labels[field]}: ${shown}`);
        }

        if (!parts.length) return [];

        const lines = [
            CONSTANTS.MESSAGES.NO_RESULTS_CONDITIONS.replace('{conditions}', parts.join('; '))
        ];
        if (parts.length >= 2) lines.push(CONSTANTS.MESSAGES.NO_RESULTS_HINT);
        return lines;
    }

    /**
     * Ставит над карточкой строку о том, что отчёт найден по шифру, а остальные условия формы
     * не учитывались. Вызывается после каждой перезаписи innerHTML карточки.
     * Отдельный класс, а не .single-result-formatted: e2e-тесты ищут карточку по нему в strict-режиме
     * @param {HTMLElement} resultsDiv - контейнер результатов
     * @param {object} row - единственная строка выдачи
     */
    static _prependByShifrNotice(resultsDiv, row) {
        const dopShifr = row["ДопШифр"] || "";
        const code = dopShifr ? `${row["Шифр"]}-${dopShifr}` : String(row["Шифр"]);
        const notice = document.createElement('div');
        notice.className = 'search-notice';
        notice.textContent = CONSTANTS.MESSAGES.BY_SHIFR_NOTICE.replace('{code}', code);
        resultsDiv.prepend(notice);
    }

    /**
     * Отображает результаты поиска в виде таблицы или карточки одного результата
     * @param {Array} rows - Массив результатов поиска
     * @param {object} [options]
     * @param {boolean} [options.byShifr] - отчёт найден запасным поиском по шифру: над карточкой пометка
     * @param {FormData|null} [options.formData] - форма поиска: пустая выдача перечисляет её поля
     * @returns {Promise<void>}
     */
    static async render(rows, { byShifr = false, formData = null } = {}) {
        console.log(`ResultsRenderer.render: получено ${rows.length} результатов для отображения`);
        
        const resultsDiv = DOMUtils.getElement(CONSTANTS.SELECTORS.RESULTS);
        if (!resultsDiv) {
            console.error('Элемент результатов не найден');
            return;
        }

        console.log('ResultsRenderer.render: элемент результатов найден');

        // Очищаем предыдущие результаты
        resultsDiv.innerHTML = '';

        if (!rows.length) {
            console.log('ResultsRenderer.render: результатов нет, показываем сообщение');
            ResultsRenderer.renderMessage(
                CONSTANTS.MESSAGES.NO_RESULTS,
                ResultsRenderer._describeConditions(formData)
            );
            return;
        }

        // Автоматическое отображение для единственного результата
        if (rows.length === 1) {
            // Для standalone PDF: handleSingleResult не делает сетевых запросов,
            // поэтому сразу рендерим обе вкладки без промежуточного Фаза-1 спиннера.
            // Для ZIP и прочих: Фаза 1 (спиннер) → Фаза 2 (полный рендер).
            const isStandalonePdf = (rows[0]["РазмерАрхива"] > 0) && (rows[0]["ТипФайла"] === CONSTANTS.FILE_TYPES.PDF);

            if (!isStandalonePdf) {
                // Фаза 1: мгновенный рендер в той же структуре tabs-container, что и Фаза 2 (без скачка)
                const routeTabLabel = CONSTANTS.TABS.CATEGORY_NAMES[0];
                let phase1 = '<div class="tabs-container">';
                phase1 += '<div class="tabs-header">';
                phase1 += `<button class="tab-button active loading" data-tab="${TAB_IDS.ROUTE}">${routeTabLabel}<span class="tab-spinner"></span></button>`;
                phase1 += '</div>';
                phase1 += `<div class="tab-content active" id="${TAB_IDS.ROUTE}">`;
                phase1 += SingleResultsRenderer.renderSingleResultFormatted(rows[0]);
                phase1 += '</div></div>';
                resultsDiv.innerHTML = phase1;
                if (byShifr) ResultsRenderer._prependByShifrNotice(resultsDiv, rows[0]);
            }

            // Фаза 2: HEAD-проверка файла + полный рендер с табами
            const archiveData = await SingleResultsRenderer.handleSingleResult(rows[0]);
            
            // Генерируем табы с новым табом "Маршрут"
            const shifr = rows[0]["Шифр"];
            const dopShifr = rows[0]["ДопШифр"] || "";
            const html = SingleResultsRenderer.renderSingleResultWithTabs(rows[0], archiveData, shifr, dopShifr);
            
            resultsDiv.innerHTML = html;
            if (byShifr) ResultsRenderer._prependByShifrNotice(resultsDiv, rows[0]);
            
            // Настраиваем обработчики для табов
            SingleResultsRenderer.setupTabHandlers();

            // Применяем состояние вкладок/файла из hash URL (если задано)
            SingleResultsRenderer.applyViewStateFromHash();

            // Подстраиваем высоту активного viewer'а под экран (только десктоп)
            requestAnimationFrame(() => SingleResultsRenderer.adjustActiveViewerHeight());
            
            console.log('ResultsRenderer.render: форматированное отображение завершено');
            return;
        }

        // Генерируем и отображаем таблицу
        console.log('ResultsRenderer.render: генерируем таблицу');
        const tableHtml = TableResultsRenderer.generateTable(rows);
        resultsDiv.innerHTML = tableHtml;
        
        console.log('ResultsRenderer.render: таблица отображена');
    }

    // --- API, используемый main.js и другими модулями ---

    static appendRowsToTable(rows) {
        return TableResultsRenderer.appendRowsToTable(rows);
    }

    static showLoadingIndicator(loaded, total) {
        return TableResultsRenderer.showLoadingIndicator(loaded, total);
    }

    static updateLoadingIndicator(loaded, total) {
        return TableResultsRenderer.updateLoadingIndicator(loaded, total);
    }

    static hideLoadingIndicator() {
        return TableResultsRenderer.hideLoadingIndicator();
    }

    static applyViewStateFromHash() {
        return SingleResultsRenderer.applyViewStateFromHash();
    }

    static adjustActiveViewerHeight() {
        return SingleResultsRenderer.adjustActiveViewerHeight();
    }
}

