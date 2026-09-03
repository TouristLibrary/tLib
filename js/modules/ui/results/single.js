// Version 3.4 - 10.07.2026 - fileStatus 'hidden' для отчётов, скрытых администратором
// Описание: Оркестратор единственного результата поиска. Делегирует рендеринг/обработку
//           viewer'ов (PDF/Треки/Изображения) соответствующим стратегиям через реестр.
//           Применяет escapeHtml для защиты от DOM-XSS при вставке данных из БД.

import { CONSTANTS, TAB_IDS } from '../../../config/constants.js';
import { FileUtils } from '../../fileUtils.js';
import { DataFormatter } from '../dataFormatter.js';
import { escapeHtml, escapeAttribute } from '../../../utils/sanitize.js';
import { getServerConfig } from '../../../services/serverConfigService.js';
import { cacheWarmService } from '../../../services/cacheWarmService.js';

import {
    parseViewStateFromHash, replaceUrlHash, clearHash,
} from './viewers/viewStateManager.js';

import { initTabs } from './viewers/tabController.js';

import {
    adjustActiveViewerHeight, findVisibleViewer,
    buildOtherLinkHtml, prepareFileLink,
} from './viewers/viewerHelpers.js';

import * as pdfViewer from './viewers/pdfViewer.js';
import * as imageViewer from './viewers/imageViewer.js';
import * as trackViewer from './viewers/trackViewer.js';

// =============================================================================
// VIEWER STRATEGY REGISTRY
// =============================================================================

/** Маппинг FILE_CATEGORIES -> стратегия viewer'а */
const VIEWER_STRATEGIES = {
    [CONSTANTS.FILE_CATEGORIES.PDF]:        pdfViewer,
    [CONSTANTS.FILE_CATEGORIES.IMAGES]:     imageViewer,
    [CONSTANTS.FILE_CATEGORIES.GPS_TRACKS]: trackViewer,
};

/** Маппинг TAB_IDS -> стратегия viewer'а */
const VIEWER_BY_TAB = {
    [TAB_IDS.PDF]: pdfViewer,
    [TAB_IDS.IMG]: imageViewer,
    [TAB_IDS.GEO]: trackViewer,
};

// =============================================================================
// FILE EXISTENCE CHECK
// =============================================================================

const FILE_CHECK_TIMEOUT_MS = 3000;

/**
 * Запускает подготовку кеша через /api/cache/.../prepare и возвращает статус + список файлов.
 * Использует собственный AbortController с коротким таймаутом (без retry) для быстрой проверки.
 * @param {string} localFileName - имя файла без расширения (e.g. "02597-TLIB")
 * @param {number} [timeoutMs] - таймаут в мс
 * @returns {Promise<{status: 'available'|'preparing'|'not_found'|'unavailable', files: object[]|null}>}
 */
async function checkFileAvailable(localFileName, timeoutMs = FILE_CHECK_TIMEOUT_MS) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const url = `${CONSTANTS.API.CACHE_BASE}/${encodeURIComponent(localFileName)}/prepare`;
        const resp = await fetch(url, { method: 'POST', signal: controller.signal });
        const data = await resp.json();
        const files = data.files || null;
        if (data.status === 'ready') return { status: 'available', files };
        if (data.status === 'started' || data.status === 'already_preparing') return { status: 'preparing', files };
        return { status: 'not_found', files: null };
    } catch {
        return { status: 'unavailable', files: null };
    } finally {
        clearTimeout(timer);
    }
}

// =============================================================================
// EXPORTED CLASS
// =============================================================================

export class SingleResultsRenderer {
    /**
     * Обрабатывает отображение единственного результата поиска.
     * Проверяет физическое наличие файла HEAD-запросом к /data/.
     * @param {object} row - Результат поиска
     * @returns {Promise<Object>} Объект с данными о файлах и типе
     */
    static async handleSingleResult(row) {
        const shifrNum = row["Шифр"];
        const dopShifrStr = row["ДопШифр"] || "";

        if (shifrNum === undefined || shifrNum === null) {
            return { files: [], isZip: false, isPdf: false };
        }

        try {
            const downloadInfo = DataFormatter.buildRowDownloadInfo(row);
            const { localFileName, fileExt, hasFile } = downloadInfo;

            let fileAvailable = hasFile;
            let fileStatus = hasFile ? 'available' : (row["Скрыт"] ? 'hidden' : 'not_found');
            let prepareFiles = null;

            if (hasFile && localFileName) {
                const result = await checkFileAvailable(localFileName);
                fileStatus = result.status;
                prepareFiles = result.files;
                if (fileStatus === 'preparing') {
                    fileAvailable = true;
                } else {
                    fileAvailable = fileStatus === 'available';
                    if (!fileAvailable && fileStatus === 'not_found') {
                        fileStatus = 'unavailable';
                    }
                }
            }

            const isZip = fileAvailable && fileExt === CONSTANTS.FILE_TYPES.ZIP;
            const isPdf = fileAvailable && fileExt === CONSTANTS.FILE_TYPES.PDF;

            const files = (isZip && prepareFiles) ? prepareFiles : [];

            return { files, isZip, isPdf, localFileName, hasFile: fileAvailable, fileStatus, prepareFiles };

        } catch (error) {
            console.error('Ошибка при обработке единственного результата:', error);
            return { files: [], isZip: false, isPdf: false };
        }
    }

    /**
     * Подписывает обработчик resize для подстройки высоты активного viewer'а.
     * Делаем это один раз, чтобы не множить обработчики при повторных рендерах.
     */
    static ensureViewerResizeHandler() {
        if (SingleResultsRenderer._viewerResizeHandler) return;
        SingleResultsRenderer._viewerResizeHandler = () => adjustActiveViewerHeight();
        window.addEventListener('resize', SingleResultsRenderer._viewerResizeHandler);
    }

    /**
     * Делегирует пересчёт высоты активного viewer'а в viewerHelpers.
     * Публичный API для index.js.
     */
    static adjustActiveViewerHeight() {
        adjustActiveViewerHeight();
    }

    /**
     * Генерирует форматированный текст для единственного результата поиска
     * @param {object} row - Результат поиска
     * @returns {string} HTML форматированного текста
     */
    static renderSingleResultFormatted(row) {
        let html = '<div class="single-result-formatted">';

        const shifrVal = row["Шифр"] ?? "";
        const dopShifrVal = row["ДопШифр"] ?? "";
        const shifrPrefix = dopShifrVal ? `#${escapeHtml(shifrVal)}-${escapeHtml(dopShifrVal)}` : `#${escapeHtml(shifrVal)}`;

        const routeText = DataFormatter.formatRoute(row);
        const firstLineText = routeText ? `${shifrPrefix} ${routeText}` : shifrPrefix;
        html += `<div class="single-result-field route-field">
            <span>${firstLineText}</span>
        </div>`;

        const combinedParts = [];

        const regionStr = DataFormatter.formatRegion(row);
        if (regionStr) combinedParts.push(regionStr);

        const type = row["Тип"] ?? "";
        const shipType = row["ТипСудна"] ?? "";

        let typeStr = escapeHtml(type);
        if (shipType) typeStr += ` (${escapeHtml(shipType)})`;

        const catStr = DataFormatter.formatCategory(row);
        if (catStr) typeStr += typeStr ? ` ${catStr} к.с.` : `${catStr} к.с.`;
        if (typeStr) combinedParts.push(typeStr);

        const periodStr = DataFormatter.formatYearMonth(row);
        if (periodStr) combinedParts.push(periodStr);

        const author = row["Автор"] ?? "";
        const city = row["Город"] ?? "";

        let authorStr = "";
        if (author && city) {
            authorStr = `${escapeHtml(author)} (${escapeHtml(city)})`;
        } else if (author) {
            authorStr = escapeHtml(author);
        } else if (city) {
            authorStr = escapeHtml(city);
        }
        if (authorStr) combinedParts.push(authorStr);

        const uploadedBy = row["ЗагрузилИмя"] ?? "";
        const uploadDate = row["ДатаВремяЗагрузки"];
        if (uploadedBy || uploadDate) {
            let uploadStr = "загрузил:";
            if (uploadedBy) uploadStr += ` ${escapeHtml(uploadedBy)}`;
            if (uploadDate) uploadStr += ` ${DataFormatter.formatLoadDate(uploadDate, false)}`;
            combinedParts.push(uploadStr);
        }

        if (combinedParts.length > 0) {
            html += `<div class="single-result-field" title="район; тип и категория; год и месяц похода; автор отчета; кто загрузил отчет" style="cursor: help;">
                <span>${combinedParts.join(' ; ')}</span>
            </div>`;
        }

        const comments = row["Комментарии"] ?? "";
        if (comments) {
            html += `<div class="single-result-field comments-field">
                <span>${escapeHtml(comments)}</span>
            </div>`;
        }

        try {
            html += DataFormatter.renderActionIconsHtml(row, 'single-result-actions');
        } catch {}

        html += '</div>';
        return html;
    }

    /**
     * Генерирует табы с файлами для единственного результата.
     * Делегирует рендеринг file-list и viewer-контейнеров стратегиям через VIEWER_STRATEGIES.
     * @param {object} row
     * @param {object} archiveData - {files, isZip, isPdf, localFileName, hasFile, fileStatus}
     * @param {string|number} shifr
     * @param {string} dopShifr
     * @returns {string}
     */
    static renderSingleResultWithTabs(row, archiveData, shifr, dopShifr) {
        const { files = [], isZip, isPdf, localFileName, hasFile, fileStatus, prepareFiles } = archiveData;

        const categories = {
            0: { name: CONSTANTS.TABS.CATEGORY_NAMES[0], files: [], isInfo: true },
            1: { name: CONSTANTS.TABS.CATEGORY_NAMES[1], files: [] },
            2: { name: CONSTANTS.TABS.CATEGORY_NAMES[2], files: [] },
            3: { name: CONSTANTS.TABS.CATEGORY_NAMES[3], files: [] },
            4: { name: CONSTANTS.TABS.CATEGORY_NAMES[4], files: [] },
        };

        if (!hasFile) {
            const displayRow = { ...row, "РазмерАрхива": 0 };
            let message;
            if (fileStatus === 'preparing') {
                message = CONSTANTS.MESSAGES.CACHE_PREPARING;
            } else if (fileStatus === 'unavailable') {
                message = CONSTANTS.MESSAGES.STORAGE_UNAVAILABLE;
            } else if (fileStatus === 'hidden') {
                message = CONSTANTS.MESSAGES.REPORT_HIDDEN;
            } else {
                message = CONSTANTS.MESSAGES.FILE_NOT_AVAILABLE;
            }

            let html = '<div class="tabs-container">';
            html += '<div class="tabs-header">';
            html += `<button class="tab-button active" data-tab="${TAB_IDS.ROUTE}">${CONSTANTS.TABS.CATEGORY_NAMES[0]}</button>`;
            html += '</div>';
            html += `<div class="tab-content active" id="${TAB_IDS.ROUTE}">`;
            html += SingleResultsRenderer.renderSingleResultFormatted(displayRow);
            html += `<div class="file-not-available-placeholder">${message}</div>`;
            html += '</div>';
            html += '</div>';
            return html;
        }

        files.forEach(file => {
            const category = FileUtils.getFileCategory(file.name);
            if (categories[category]) categories[category].files.push(file);
        });

        if (isPdf && localFileName) {
            const pdfUrl = `${window.location.origin}${getServerConfig().paths.pdfApi}/${localFileName}.pdf`;
            const entry = {
                name: `${localFileName}.pdf`,
                size: row["РазмерАрхива"] || 0,
                download_url: pdfUrl,
            };
            const prepEntry = prepareFiles?.find(f => f.kind === 'pdf');
            if (prepEntry?.pages) entry.pages = prepEntry.pages;
            if (prepEntry?.png_dir) entry.png_dir = prepEntry.png_dir;
            categories[1].files.push(entry);
        }

        const nonEmptyCategories = [
            { id: '0', ...categories[0] },
            ...Object.entries(categories)
                .filter(([id, cat]) => id !== '0' && cat.files.length > 0)
                .map(([id, cat]) => ({ id, ...cat })),
        ];

        const archiveNameAttr = localFileName ? ` data-archive-name="${escapeAttribute(localFileName)}"` : '';
        let html = `<div class="tabs-container"${archiveNameAttr}>`;

        html += '<div class="tabs-header">';
        nonEmptyCategories.forEach((cat, index) => {
            const activeClass = index === 0 ? ' active' : '';
            const spinnerHtml = cat.id !== '0' ? '<span class="tab-spinner"></span>' : '';
            html += `<button class="tab-button${activeClass}" data-tab="tab-${cat.id}">${cat.name}${spinnerHtml}</button>`;
        });
        html += '</div>';

        const isPlainMode = document.documentElement.classList.contains(CONSTANTS.CSS_CLASSES.PLAIN_MODE);

        nonEmptyCategories.forEach((cat, index) => {
            const catId = parseInt(cat.id);
            const activeClass = index === 0 ? ' active' : '';
            html += `<div class="tab-content${activeClass}" id="tab-${cat.id}">`;

            if (cat.id === '0') {
                html += SingleResultsRenderer.renderSingleResultFormatted(row);
                html += '</div>';
                return;
            }

            const strategy = VIEWER_STRATEGIES[catId];
            const strategyCtx = { shifr, dopShifr, localFileName, isZip, archiveName: localFileName || '', isPlainMode };

            html += '<div class="file-list-inline">';
            if (strategy) {
                html += strategy.buildFileListHtml(cat.files, strategyCtx);
            } else {
                // Категория «Прочее» — нет viewer'а
                const otherLinks = cat.files.map(file => {
                    const prep = prepareFileLink(file);
                    if (!prep.validation.valid) {
                        console.warn(`Invalid download_url for file "${prep.fullName}": ${prep.validation.reason}`);
                        return `<span class="disabled" title="${CONSTANTS.MESSAGES.INVALID_URL}">${prep.escapedDisplayName}</span>`;
                    }
                    return buildOtherLinkHtml(prep);
                });
                html += otherLinks.join('; ');
            }
            html += '</div>';

            if (strategy && cat.files.length > 0) {
                html += strategy.buildViewersHtml(cat.files, strategyCtx);
            }

            html += '</div>';
        });

        html += '</div>';
        return html;
    }

    /**
     * Настраивает обработчики переключения табов.
     * Делегирует DOM-механику в tabController, бизнес-хуки — в onSwitch.
     */
    static setupTabHandlers() {
        const tabs = initTabs({
            buttonSelector: '.tab-button',
            contentSelector: '.tab-content',
            activeClass: CONSTANTS.CSS_CLASSES.ACTIVE,
            onSwitch: (targetTabId, targetContent, button) => {
                const strategy = VIEWER_BY_TAB[targetTabId];

                // Делегируем autoLoad стратегии
                if (strategy && targetContent) {
                    strategy.autoLoad(targetContent);
                }

                // Прогрев кеша при переключении на viewer-таб
                const tabsContainer = button?.closest('.tabs-container');
                const archiveName = tabsContainer?.dataset?.archiveName;
                if (archiveName && targetTabId && targetTabId !== TAB_IDS.ROUTE) {
                    cacheWarmService.prepareCache(archiveName).catch(err =>
                        console.warn('Cache prepare failed:', err)
                    );
                }

                // Спиннер на кнопке таба + ожидание загрузки контента
                if (strategy && button) {
                    button.classList.add(CONSTANTS.CSS_CLASSES.LOADING);
                    SingleResultsRenderer._waitForTabContentLoad(targetTabId, button);
                }

                // Hash URL: делегируем стратегии или очищаем для Маршрута
                const prevState = parseViewStateFromHash();
                if (!targetTabId || targetTabId === TAB_IDS.ROUTE) {
                    clearHash();
                } else if (strategy?.getHashStateOnTabSwitch) {
                    replaceUrlHash(strategy.getHashStateOnTabSwitch(targetTabId, prevState));
                }

                // Пересчёт высоты и post-activation hook (для PDF — фокус viewer'а)
                requestAnimationFrame(() => {
                    adjustActiveViewerHeight();
                    if (strategy?.onTabActivated) {
                        strategy.onTabActivated();
                    }
                });
            },
        });

        SingleResultsRenderer._tabController = tabs;

        // Делегируем setupHandlers всем стратегиям
        Object.values(VIEWER_STRATEGIES).forEach(s => s.setupHandlers());

        SingleResultsRenderer.ensureViewerResizeHandler();
    }

    /**
     * Применяет состояние вкладок/файлов из hash URL к UI единственного результата.
     * Поддерживает: #tab=pdf|geo|img|etc[&file=...][&p=...]
     * Делегирует applyHashState стратегии через VIEWER_BY_TAB.
     */
    static applyViewStateFromHash() {
        try {
            const state = parseViewStateFromHash();
            const tabToken = state.tab;
            if (!tabToken) return;

            const targetTabId = CONSTANTS.TABS.ID_BY_TOKEN[String(tabToken)] || null;
            if (!targetTabId) return;

            // Программное переключение через tabController (вместо tabButton.click())
            if (!SingleResultsRenderer._tabController) return;
            SingleResultsRenderer._tabController.switchTo(targetTabId);

            const tabContent = document.getElementById(targetTabId);
            const strategy = VIEWER_BY_TAB[targetTabId];
            if (strategy?.applyHashState && tabContent) {
                strategy.applyHashState(tabContent, state);
            }
        } catch (error) {
            console.warn('applyViewStateFromHash: ошибка применения состояния:', error);
        }
    }

    /**
     * Ожидает загрузки контента таба и убирает спиннер с кнопки.
     * @param {string} tabId
     * @param {HTMLElement} tabButton
     */
    static _waitForTabContentLoad(tabId, tabButton) {
        const tabContent = document.getElementById(tabId);
        if (!tabContent) {
            tabButton.classList.remove(CONSTANTS.CSS_CLASSES.LOADING);
            return;
        }

        const removeLoading = () => tabButton.classList.remove(CONSTANTS.CSS_CLASSES.LOADING);

        let elements = [];
        const visible = findVisibleViewer(tabContent, tabId);
        if (visible) {
            const hasContent = visible.tagName === 'IMG'
                ? visible.src?.includes('/api/')
                : visible.src && !visible.src.includes('about:blank');
            if (hasContent) elements = [visible];
        }

        if (!elements.length) {
            removeLoading();
            return;
        }

        const el = elements[0];

        const isLoaded = el.tagName === 'IMG'
            ? el.complete && el.naturalWidth > 0
            : (el.contentDocument?.readyState === 'complete' || el.contentWindow);

        if (isLoaded) {
            removeLoading();
            return;
        }

        el.addEventListener('load', removeLoading, { once: true });
        el.addEventListener('error', removeLoading, { once: true });
        setTimeout(removeLoading, CONSTANTS.TIMING.TAB_LOAD_FALLBACK_TIMEOUT);
    }

}
