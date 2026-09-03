// Version 2.0 - 25.03.2026
// Описание: Рендерит табличные результаты поиска: генерация HTML таблицы, инкрементальное добавление строк,
//           индикатор фоновой догрузки и блокировка контролов сортировки на время загрузки.
//           Сортировка выполняется на сервере через параметры sortColumn/sortOrder в форме поиска.

import { CONSTANTS, TABLE_HEADERS, TABLE_COLUMN_STYLES } from '../../../config/constants.js';
import { DOMUtils } from '../utils.js';
import { DataFormatter } from '../dataFormatter.js';

/**
 * Генерирует HTML текста индикатора загрузки
 * @param {number} loaded - Количество загруженных записей
 * @param {number} total - Общее количество записей
 * @returns {string} HTML строка с прогрессом
 */
function formatLoadingHtml(loaded, total) {
    return `⏳ ${CONSTANTS.MESSAGES.LOADING_MORE}<br>Загружено ${loaded.toLocaleString('ru-RU')} из ${total.toLocaleString('ru-RU')}`;
}

export class TableResultsRenderer {
    /**
     * Генерирует HTML таблицы результатов
     * @param {Array} rows - Массив результатов поиска (уже отсортированных сервером)
     * @returns {string} HTML таблицы
     */
    static generateTable(rows) {
        let table = `<table id="${CONSTANTS.SELECTORS.RESULTS_TABLE.substring(1)}">
            <colgroup>`;

        TABLE_HEADERS.forEach(header => {
            const width = TABLE_COLUMN_STYLES[header.key];
            table += `<col style="width:${width};">`;
        });

        table += `</colgroup>
            <thead>
            <tr>`;

        TABLE_HEADERS.forEach(header => {
            const titleAttr = header.title ? ` title="${header.title}"` : '';
            table += `<th${titleAttr}>${header.name}</th>`;
        });

        table += "</tr></thead><tbody>";

        rows.forEach(row => {
            const rowInfo = DataFormatter.buildRowDownloadInfo(row);
            table += "<tr>";
            TABLE_HEADERS.forEach(header => {
                table += `<td>${DataFormatter.getCellContent(row, header, rowInfo)}</td>`;
            });
            table += "</tr>";
        });

        table += "</tbody></table>";
        return table;
    }

    /**
     * Инкрементально добавляет строки в существующую таблицу без перерисовки
     * @param {Array} rows - Массив новых результатов для добавления
     */
    static appendRowsToTable(rows) {
        const tbody = document.querySelector(`${CONSTANTS.SELECTORS.RESULTS_TABLE} tbody`);
        if (!tbody) {
            console.warn('Таблица не найдена для добавления строк');
            return;
        }

        const scrollY = window.scrollY;

        rows.forEach(row => {
            const rowInfo = DataFormatter.buildRowDownloadInfo(row);
            const tr = document.createElement('tr');
            TABLE_HEADERS.forEach(header => {
                const td = document.createElement('td');
                td.innerHTML = DataFormatter.getCellContent(row, header, rowInfo);
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });

        window.scrollTo(0, scrollY);

        console.log(`Добавлено ${rows.length} строк в таблицу`);
    }

    /**
     * Показывает индикатор загрузки под таблицей и блокирует контролы сортировки
     * @param {number} loaded - Количество загруженных записей
     * @param {number} total - Общее количество записей
     */
    static showLoadingIndicator(loaded, total) {
        let indicator = document.querySelector(CONSTANTS.SELECTORS.LOADING_INDICATOR);
        if (!indicator) {
            indicator = document.createElement('div');
            indicator.id = CONSTANTS.SELECTORS.LOADING_INDICATOR.substring(1);
            indicator.className = 'results-loading-indicator';

            const resultsDiv = document.querySelector(CONSTANTS.SELECTORS.RESULTS);
            if (resultsDiv) {
                resultsDiv.appendChild(indicator);
            }
        }

        indicator.innerHTML = formatLoadingHtml(loaded, total);
    }

    /**
     * Обновляет индикатор загрузки с новыми значениями прогресса
     * @param {number} loaded - Количество загруженных записей
     * @param {number} total - Общее количество записей
     */
    static updateLoadingIndicator(loaded, total) {
        const indicator = document.querySelector(CONSTANTS.SELECTORS.LOADING_INDICATOR);
        if (indicator) {
            indicator.innerHTML = formatLoadingHtml(loaded, total);
        }
    }

    /**
     * Скрывает индикатор загрузки и восстанавливает контролы сортировки
     */
    static hideLoadingIndicator() {
        const indicator = document.querySelector(CONSTANTS.SELECTORS.LOADING_INDICATOR);
        if (indicator) {
            indicator.remove();
        }

    }
}
