// Version 1.10 - 10.07.2026
// Описание: Форматирует строки результатов для UI. Генерирует HTML для ячеек таблицы и ссылки для автопоиска, нормализует шифры и даты.
//           Применяет escapeHtml для защиты от DOM-XSS при вставке данных из БД.
//           Применяет validateResourceUrl для защиты href от javascript:/data:/внешних URL при компрометации источника данных.
// 1.10: buildRowDownloadInfo — hasFile учитывает row["Скрыт"] (скрытые отчёты администратором:
//       ссылка/иконка скачивания гаснут, как при отсутствии файла).
// 1.8: удалён неиспользуемый static getArchiveSize() и устаревший JSDoc-фрагмент после него.

import { CONSTANTS, MONTHS_RU } from '../../config/constants.js';
import { appState } from '../../core/appState.js';
import { FileUtils } from '../fileUtils.js';
import { escapeHtml, escapeAttribute, validateResourceUrl } from '../../utils/sanitize.js';
import { getServerConfig } from '../../services/serverConfigService.js';
import { buildShareUrlFromText } from '../../utils/urlUtils.js';

/**
 * Генератор данных для отображения в таблице
 */
export class DataFormatter {
    /**
     * Форматирует строку района в формате "РайонОбщий: Район" (или одно из значений)
     * Результат HTML-экранирован для безопасной вставки в DOM.
     * @param {object} row - Строка данных из БД
     * @returns {string} Экранированная строка района
     */
    static formatRegion(row) {
        const regionObshiy = row["РайонОбщий"] ?? "";
        const region = row["Район"] ?? "";
        if (regionObshiy && region) {
            return `${escapeHtml(regionObshiy)}: ${escapeHtml(region)}`;
        } else if (regionObshiy) {
            return escapeHtml(regionObshiy);
        } else {
            return escapeHtml(region);
        }
    }

    /**
     * Форматирует маршрут: экранирует HTML и вставляет zero-width space после символов '='
     * @param {object} row - Строка данных из БД
     * @returns {string} Экранированная строка маршрута
     */
    static formatRoute(row) {
        const raw = row["Маршрут"] ?? "";
        return escapeHtml(String(raw).replace(/=/g, '=\u200B'));
    }

    /**
     * Форматирует диапазон категорий "КатегорияС - КатегорияПо" (или одно из значений)
     * @param {object} row - Строка данных из БД
     * @returns {string} Экранированная строка категории
     */
    static formatCategory(row) {
        const from = row["КатегорияС"] ?? "";
        const to = row["КатегорияПо"] ?? "";
        return `${escapeHtml(from)}${from && to ? " - " : ""}${escapeHtml(to)}`;
    }

    /**
     * Формирует текст ссылки в формате "Шифр" или "Шифр-ДопШифр"
     * @param {string|number} shifr - Основной шифр
     * @param {string} dopShifr - Дополнительный шифр (опционально)
     * @returns {string} Строка формата "Шифр" или "Шифр-ДопШифр"
     */
    static buildLinkText(shifr, dopShifr) {
        const dop = dopShifr ?? '';
        return String(shifr ?? '') + (dop ? `-${dop}` : '');
    }

    static safeEncode(value) {
        if (typeof value !== 'string') return '';
        return value.replace(CONSTANTS.REGEX.SAFE_ENCODE_CHARS, (ch) => encodeURIComponent(ch));
    }

    /**
     * Преобразует шифр в 5-значный формат
     * @param {string|number} shifr - Шифр для преобразования
     * @returns {string|object} Преобразованный шифр или объект с ошибкой
     */
    static toShifr5(shifr) {
        if (shifr === undefined || shifr === null || String(shifr).trim() === '') {
            throw new Error("Пустое значение Шифр");
        }
        const clean = String(shifr).trim();
        if (!CONSTANTS.REGEX.DIGITS_ONLY.test(clean)) {
            throw new Error("Шифр содержит недопустимые символы");
        }
        return clean.padStart(CONSTANTS.LIMITS.SHIFR_PAD_LENGTH, '0');
    }

    /**
     * Извлекает download-метаданные строки: шифр, URL скачивания, share URL.
     * Единая точка формирования этих данных — используется в renderShifrLinkCell,
     * renderRouteLinkCell и renderSingleResultFormatted (single.js).
     * @param {object} row - Строка данных из БД
     * @returns {{shifr5: string, dop: string, linkText: string, encodedDop: string,
     *            fileExt: string, hasFile: boolean, downloadUrl: string|null,
     *            validatedUrl: string|null, downloadTitle: string, shareUrl: string}}
     */
    static buildRowDownloadInfo(row) {
        const shifr5 = DataFormatter.toShifr5(row["Шифр"]);
        const dop = row["ДопШифр"];
        const linkText = DataFormatter.buildLinkText(row["Шифр"], dop);
        const dopTrimmed = dop && String(dop).trim() !== "" ? String(dop).trim() : "";
        const encodedDop = dopTrimmed ? `-${DataFormatter.safeEncode(dopTrimmed)}` : "";
        const localFileName = dopTrimmed ? `${shifr5}-${DataFormatter.safeEncode(dopTrimmed)}` : shifr5;
        const fileExt = row["ТипФайла"];
        const hasFile = row["РазмерАрхива"] > 0 && !row["Скрыт"];
        const shareUrl = buildShareUrlFromText(linkText);

        let downloadUrl = null;
        if (hasFile) {
            const fileName = `${shifr5}${encodedDop}.${fileExt}`;
            downloadUrl = `${getServerConfig().paths.localArchive}/${fileName}`;
        }

        let validatedUrl = null;
        let downloadTitle = '';
        if (hasFile && downloadUrl) {
            const v = validateResourceUrl(downloadUrl, 'default');
            if (v.valid) {
                validatedUrl = v.url;
                downloadTitle = DataFormatter._buildDownloadTitle(v.url, row["РазмерАрхива"] || 0);
            }
        }

        return { shifr5, dop, linkText, encodedDop, localFileName, fileExt, hasFile, downloadUrl, validatedUrl, downloadTitle, shareUrl, archiveSize: row["РазмерАрхива"] || 0 };
    }

    /**
     * Формирует атрибут title для кнопки/ссылки скачивания
     * @param {string} validUrl - Валидированный URL файла
     * @param {number} archiveSize - Размер архива в байтах
     * @returns {string} Строка вида "Скачать отчет\n<имя файла> (<размер>)"
     */
    static _buildDownloadTitle(validUrl, archiveSize) {
        const fileName = validUrl.split('/').pop();
        const fileSizeStr = FileUtils.formatSizeInMb(archiveSize);
        return `Скачать отчет&#10;${escapeAttribute(fileName)}${fileSizeStr}`;
    }

    /**
     * Генерирует HTML контейнера иконок «поделиться» и «скачать/недоступно».
     * Использует validatedUrl/downloadTitle из info (валидация выполняется в buildRowDownloadInfo).
     * Единая точка рендера иконок для таблицы и карточки.
     * @param {object} row - Строка данных из БД
     * @param {string} [containerClass] - CSS класс контейнера (по умолчанию 'shifr-actions')
     * @returns {string} HTML строка
     */
    static renderActionIconsHtml(row, containerClass = 'shifr-actions', _cachedInfo = null) {
        const info = _cachedInfo || DataFormatter.buildRowDownloadInfo(row);
        const { hasFile, validatedUrl, downloadTitle, shareUrl } = info;
        const shareIcon = DataFormatter.renderShareIconHtml(shareUrl);

        if (!hasFile) {
            return `<div class="${containerClass}">
                    ${shareIcon}
                    ${DataFormatter.renderDisabledDownloadIconHtml()}
                </div>`;
        }

        if (!validatedUrl) {
            return `<div class="${containerClass}">
                    ${shareIcon}
                    ${DataFormatter.renderDisabledDownloadIconHtml(CONSTANTS.MESSAGES.INVALID_URL)}
                </div>`;
        }

        return `<div class="${containerClass}">
                    ${shareIcon}
                    ${DataFormatter.renderDownloadIconHtml(validatedUrl, downloadTitle)}
                </div>`;
    }

    /**
     * Генерирует HTML иконки «поделиться» (копировать ссылку)
     * @param {string} shareUrl - URL для копирования
     * @returns {string} HTML элемента
     */
    static renderShareIconHtml(shareUrl) {
        return `<a href="#" class="shifr-action-icon copy-report-link" data-share-url="${shareUrl}" title="Скопировать ссылку на отчет">
                        <svg width="18" height="18"><use href="${CONSTANTS.ICONS.SHARE}"></use></svg>
                    </a>`;
    }

    /**
     * Генерирует HTML неактивной иконки «скачать» (файл недоступен или URL невалиден)
     * @param {string} [title] - Текст подсказки (по умолчанию FILE_NOT_AVAILABLE)
     * @returns {string} HTML элемента
     */
    static renderDisabledDownloadIconHtml(title) {
        const t = title ?? CONSTANTS.MESSAGES.FILE_NOT_AVAILABLE;
        return `<span class="shifr-action-icon disabled" title="${t}">
                        <svg width="18" height="18"><use href="${CONSTANTS.ICONS.DOWNLOAD}"></use></svg>
                    </span>`;
    }

    /**
     * Генерирует HTML активной иконки «скачать»
     * @param {string} url - Валидированный URL файла
     * @param {string} downloadTitle - Атрибут title (может содержать HTML entities)
     * @returns {string} HTML элемента
     */
    static renderDownloadIconHtml(url, downloadTitle) {
        return `<a href="${url}" class="shifr-action-icon download-report" download title="${downloadTitle}">
                        <svg width="18" height="18"><use href="${CONSTANTS.ICONS.DOWNLOAD}"></use></svg>
                    </a>`;
    }

    /**
     * Генерирует HTML ссылку для ячейки с шифром
     * @param {object} row - Строка данных из БД
     * @returns {string} HTML ссылки или сообщение об ошибке
     */
    static renderShifrLinkCell(row, info = null) {
        try {
            info = info || DataFormatter.buildRowDownloadInfo(row);
            const { shifr5, linkText, hasFile, validatedUrl, downloadTitle } = info;

            if (!shifr5 || shifr5.length !== 5) {
                throw new Error("Ошибка формирования Шифр5");
            }

            if (!hasFile || !validatedUrl) {
                if (hasFile && !validatedUrl) {
                    console.warn(`Invalid download URL for ${linkText}`);
                }
                return `<div class="shifr-cell-container">
                <span class="shifr-link">${escapeHtml(linkText)}</span>
                ${DataFormatter.renderActionIconsHtml(row, 'shifr-actions', info)}
            </div>`;
            }

            return `<div class="shifr-cell-container">
                <a href="${validatedUrl}" class="report-link shifr-link" download title="${downloadTitle}">${escapeHtml(linkText)}</a>
                ${DataFormatter.renderActionIconsHtml(row, 'shifr-actions', info)}
            </div>`;
        } catch (error) {
            return `<span style="color:red;">${error.message}</span>`;
        }
    }

    /**
     * Форматирует отображение года и месяца
     * @param {object} row - Строка данных из БД
     * @returns {string} Отформатированная строка
     */
    static formatYearMonth(row) {
        const year = row["Год"] ? String(row["Год"]) : "";
        const monthFrom = row["МесяцС"] ? MONTHS_RU[Number(row["МесяцС"]) - 1] : "";
        const monthTo = row["МесяцПо"] ? MONTHS_RU[Number(row["МесяцПо"]) - 1] : "";

        // Формируем строку месяцев
        let monthStr = "";
        if (monthFrom && monthTo) {
            monthStr = `${monthFrom}-${monthTo}`;
        } else if (monthFrom) {
            monthStr = monthFrom;
        } else if (monthTo) {
            monthStr = monthTo;
        }

        // Комбинируем год и месяцы
        if (year && monthStr) {
            return `${year} ${monthStr}`;
        }
        if (year) {
            return year;
        }
        return monthStr;
    }

    /**
     * Форматирует дату загрузки в формат \"Год Мес Число\"
     * @param {string} dateTimeStr - Строка даты в формате ISO
     * @returns {string} Отформатированная дата или пустая строка
     */
    static formatLoadDate(dateTimeStr, yearFirst = true) {
        if (!dateTimeStr) return "";
        try {
            const date = new Date(dateTimeStr);
            if (isNaN(date.getTime())) return "";
            const year = date.getFullYear();
            const month = MONTHS_RU[date.getMonth()];
            const day = String(date.getDate()).padStart(2, '0');
            return yearFirst ? `${year} ${month} ${day}` : `${day} ${month} ${year}`;
        } catch {
            return "";
        }
    }

    /**
     * Получает содержимое ячейки таблицы для конкретного заголовка
     * @param {object} row - Строка данных из БД
     * @param {object} header - Объект заголовка колонки
     * @param {object|null} [rowInfo] - Предварительно вычисленный buildRowDownloadInfo (опционально)
     * @returns {string} Содержимое ячейки
     */
    static getCellContent(row, header, rowInfo = null) {
        switch (header.key) {
            case "Шифр-ДопШифр":
                return DataFormatter.renderShifrLinkCell(row, rowInfo);
            case "Автор, Город":
                return `${escapeHtml(row["Автор"])}${row["Автор"] && row["Город"] ? ", " : ""}${escapeHtml(row["Город"])}`;
            case "Год Месяц":
                return DataFormatter.formatYearMonth(row);
            case "Категория":
                return DataFormatter.formatCategory(row);
            case "Тип, тип судна":
                return `${escapeHtml(row["Тип"])}${row["Тип"] && row["ТипСудна"] ? ", " : ""}${escapeHtml(row["ТипСудна"])}`;
            case "Маршрут":
                return DataFormatter.renderRouteLinkCell(row, rowInfo);
            case "ДатаВремяЗагрузки":
                return DataFormatter.formatLoadDate(row["ДатаВремяЗагрузки"]);
            case "Район":
                return DataFormatter.formatRegion(row);
            default:
                return escapeHtml(row[header.key]);
        }
    }

    /**
     * Генерирует кликабельный маршрут для автопоиска
     * @param {object} row - Строка данных из БД
     * @returns {string} HTML ссылки маршрута
     */
    static renderRouteLinkCell(row, info = null) {
        try {
            const { shareUrl: queryUrl } = info || DataFormatter.buildRowDownloadInfo(row);
            const routeText = DataFormatter.formatRoute(row);
            return `<a href="${queryUrl}" class="report-link" title="${queryUrl}">${routeText}</a>`;
        } catch (error) {
            return DataFormatter.formatRoute(row);
        }
    }
}

