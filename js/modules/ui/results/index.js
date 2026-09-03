// Version 1.0 - 07.01.2026 09:55:58 GMT
// Описание: Объединяет рендер результатов поиска. Выбирает режим таблицы или карточки одного результата,
//           и проксирует методы таблицы/карточки, сохраняя публичный API ResultsRenderer.

import { CONSTANTS, TAB_IDS } from '../../../config/constants.js';
import { DOMUtils } from '../utils.js';
import { TableResultsRenderer } from './table.js';
import { SingleResultsRenderer } from './single.js';

export class ResultsRenderer {
    /**
     * Отображает результаты поиска в виде таблицы или карточки одного результата
     * @param {Array} rows - Массив результатов поиска
     * @returns {Promise<void>}
     */
    static async render(rows) {
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
            resultsDiv.innerHTML = `<div class="single-result-formatted"><div class="single-result-field"><span>${CONSTANTS.MESSAGES.NO_RESULTS}</span></div></div>`;
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
            }

            // Фаза 2: HEAD-проверка файла + полный рендер с табами
            const archiveData = await SingleResultsRenderer.handleSingleResult(rows[0]);
            
            // Генерируем табы с новым табом "Маршрут"
            const shifr = rows[0]["Шифр"];
            const dopShifr = rows[0]["ДопШифр"] || "";
            const html = SingleResultsRenderer.renderSingleResultWithTabs(rows[0], archiveData, shifr, dopShifr);
            
            resultsDiv.innerHTML = html;
            
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

