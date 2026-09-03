// Version 1.2 - 20.02.2026 - openOriginal вынесен в archiveFileService.js
// Описание: Shared утилиты для всех viewer-стратегий (PDF/Image/Track):
//   - DOM-утилиты (setActiveLink, showSingleContainer, queryLinks, setupClickDelegates, findLinkByDataset, safeFocusElement)
//   - HTML-строители (spinnerPlaceholderHtml, prepareFileLink, prepareLinkContext, buildViewersBlockHtml, buildOtherLinkHtml, SVG-иконки)
//   - Viewer lifecycle (activateViewerIframe, showLoadingMessage, showViewerError, PLACEHOLDER_SELECTOR, STAGE_TO_MESSAGE)
//   - Async loading (resolveAndWait)
//   - Height management (adjustActiveViewerHeight, scheduleHeightAdjust, scheduleHeightAdjustOnLoad, VIEWER_CONFIGS, findVisibleViewer, parsePx)
//   - Viewer switch (prepareViewerSwitch — общий для PDF и Image)

import { CONSTANTS, TAB_IDS } from '../../../../config/constants.js';
import { sleep } from '../../../../utils/freeze.js';
import { FileUtils } from '../../../fileUtils.js';
import { escapeHtml, escapeAttribute, validateResourceUrl } from '../../../../utils/sanitize.js';
import { cacheWarmService } from '../../../../services/cacheWarmService.js';

// =============================================================================
// DOM UTILITIES
// =============================================================================

/**
 * Фокусирует элемент с preventScroll, с fallback без него.
 * @param {HTMLElement} el - Элемент для фокуса
 */
export function safeFocusElement(el) {
    if (!el) return;
    try { el.focus({ preventScroll: true }); }
    catch { el.focus(); }
}

/**
 * Снимает активный класс со всех ссылок и устанавливает его на targetLink.
 * @param {NodeList|HTMLElement[]} allLinks - Все ссылки в группе
 * @param {HTMLElement} targetLink - Ссылка, которую нужно сделать активной
 */
export function setActiveLink(allLinks, targetLink) {
    allLinks.forEach(link => link.classList.remove(CONSTANTS.CSS_CLASSES.ACTIVE));
    if (targetLink) targetLink.classList.add(CONSTANTS.CSS_CLASSES.ACTIVE);
}

/**
 * Показывает целевой контейнер и скрывает все остальные контейнеры с тем же атрибутом.
 * @param {string} allSelector - CSS-селектор для выборки всех контейнеров
 * @param {Element} targetContainer - Контейнер, который нужно показать
 * @param {Element} tabContent - Родительский элемент для поиска контейнеров
 */
function showSingleContainer(allSelector, targetContainer, tabContent) {
    tabContent.querySelectorAll(allSelector).forEach(container => {
        container.style.display = container === targetContainer ? '' : 'none';
    });
}

/**
 * Возвращает NodeList элементов по селектору в рамках context (или document как fallback).
 * @param {Element|null} context
 * @param {string} selector
 * @returns {NodeList}
 */
export function queryLinks(context, selector) {
    return context?.querySelectorAll?.(selector) || document.querySelectorAll(selector);
}

/**
 * Навешивает preventDefault + handler на все элементы по селектору.
 * @param {string} selector - CSS-селектор ссылок
 * @param {function(HTMLElement): void} handler - callback, получает кликнутый элемент
 */
export function setupClickDelegates(selector, handler) {
    document.querySelectorAll(selector).forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            handler(this);
        });
    });
}

/**
 * Находит целевую ссылку по dataset-значению или возвращает активную/первую ссылку.
 * @param {NodeList|HTMLElement[]} searchLinks - Список ссылок для поиска по dataset
 * @param {string} datasetKey - Ключ dataset (например 'pdfName', 'imageName', 'trackName')
 * @param {string} wantFile - Искомое значение (из hash state.file)
 * @param {Element} tabContent - Контейнер вкладки для поиска активной ссылки
 * @param {string} activeSelector - CSS-селектор активной ссылки (fallback)
 * @param {NodeList|HTMLElement[]} [fallbackLinks] - Список для финального fallback
 * @returns {HTMLElement|null}
 */
export function findLinkByDataset(searchLinks, datasetKey, wantFile, tabContent, activeSelector, fallbackLinks) {
    let target = null;
    if (wantFile && searchLinks.length > 1) {
        for (const link of searchLinks) {
            if (String(link?.dataset?.[datasetKey] || '') === wantFile) {
                target = /** @type {HTMLElement} */ (link);
                break;
            }
        }
    }
    if (!target) {
        const activeLink = tabContent.querySelector(activeSelector);
        target = /** @type {HTMLElement} */ (activeLink || (fallbackLinks || searchLinks)[0]);
    }
    return target;
}

// =============================================================================
// HEIGHT MANAGEMENT
// =============================================================================

/**
 * Конфиг viewer'ов: единственное место для CSS-селекторов поиска видимого viewer'а.
 * Используется в findVisibleViewer() и adjustActiveViewerHeight().
 */
const VIEWER_CONFIGS = [
    {
        tabId: TAB_IDS.PDF,
        containerAttr: 'data-pdf-name',
        hiddenFilter: ':not([style*="display: none"]):not([style*="display:none"])',
        viewerClass: '.png-viewer',
    },
    {
        tabId: TAB_IDS.IMG,
        containerAttr: 'data-image-name',
        hiddenFilter: ':not([style*="display: none"]):not([style*="display:none"])',
        viewerClass: '.image-viewer',
    },
    {
        tabId: TAB_IDS.GEO,
        containerAttr: 'data-track-name',
        hiddenFilter: ':not(.track-offscreen)',
        viewerClass: '.nakarte-viewer',
    },
];

/**
 * Находит видимый viewer в заданном контейнере.
 * Если tabId задан — ищет только в конфиге для этого таба, иначе перебирает все.
 * @param {Element} tabContent - Контейнер для поиска (активный таб)
 * @param {string|null} [tabId] - ID таба (TAB_IDS.*), опционально
 * @returns {Element|null}
 */
export function findVisibleViewer(tabContent, tabId = null) {
    const configs = tabId
        ? VIEWER_CONFIGS.filter(c => c.tabId === tabId)
        : VIEWER_CONFIGS;
    for (const cfg of configs) {
        const el = tabContent.querySelector(
            `.viewer-container[${cfg.containerAttr}]${cfg.hiddenFilter} ${cfg.viewerClass}`
        );
        if (el) return el;
    }
    return tabId ? null : tabContent.querySelector('.viewer');
}

/**
 * Безопасно парсит строку вида "65px" -> 65.
 * @param {string} value
 * @param {number} fallback
 * @returns {number}
 */
function parsePx(value, fallback) {
    const n = Number.parseFloat(String(value).replace('px', '').trim());
    return Number.isFinite(n) ? n : fallback;
}

/**
 * Динамически подстраивает высоту активного viewer'а под доступное пространство экрана.
 * - На десктопе: нижняя граница viewer'а имеет фиксированный отступ от низа окна (CSS-переменная)
 * - На мобильных: сбрасываем inline высоту и полагаемся на CSS (vh/переменные)
 */
export function adjustActiveViewerHeight() {
    try {
        // plain-mode: высоту viewer задаёт CSS (fullscreen), JS подстройка только мешает.
        if (document.documentElement.classList.contains(CONSTANTS.CSS_CLASSES.PLAIN_MODE)) return;

        const isMobile = window.innerWidth <= CONSTANTS.BREAKPOINTS.DESKTOP;

        const activeTab = document.querySelector('.tab-content.active');
        if (!activeTab) return;

        const viewer = findVisibleViewer(activeTab);
        if (!viewer) return;

        // Не пересчитываем высоту для скрытых viewer'ов
        if (!viewer.offsetHeight) return;

        if (isMobile) {
            viewer.style.height = '';
            return;
        }

        const cssVar = getComputedStyle(document.documentElement)
            .getPropertyValue('--viewer-bottom-offset-desktop')
            .trim();
        const bottomOffsetPx = parsePx(cssVar, CONSTANTS.SIZES.VIEWER_BOTTOM_OFFSET_FALLBACK);

        const rectTop = viewer.getBoundingClientRect().top;
        const availableHeight = Math.max(0, window.innerHeight - rectTop - bottomOffsetPx);

        viewer.style.height = `${availableHeight}px`;
    } catch (error) {
        console.error('adjustActiveViewerHeight: ошибка подстройки высоты viewer:', error);
    }
}

/**
 * Планирует пересчёт высоты активного viewer'а через requestAnimationFrame.
 */
export function scheduleHeightAdjust() {
    requestAnimationFrame(adjustActiveViewerHeight);
}

/**
 * Планирует пересчёт высоты сейчас и ещё раз после загрузки элемента.
 * @param {HTMLElement} element - iframe или img, который будет загружаться
 */
export function scheduleHeightAdjustOnLoad(element) {
    scheduleHeightAdjust();
    element.addEventListener('load', () => scheduleHeightAdjust(), { once: true });
}

// =============================================================================
// VIEWER SWITCH (shared by PDF and Image strategies)
// =============================================================================

/**
 * Выполняет общий скелет выбора viewer'а: находит контейнер и viewer по имени файла,
 * переключает active-класс ссылок и показывает нужный контейнер.
 * Используется в pdfViewer.selectLink и imageViewer.selectLink.
 *
 * @param {HTMLElement} linkEl - Кликнутая ссылка файла
 * @param {{nameDataset: string, containerDataAttr: string, viewerSelector: string, linkSelector: string}} config
 * @returns {{name: string, tabContent: Element, targetContainer: Element, viewer: Element, multi: boolean}|null}
 */
export function prepareViewerSwitch(linkEl, { nameDataset, containerDataAttr, viewerSelector, linkSelector }) {
    if (!linkEl) return null;

    const name = linkEl.dataset?.[nameDataset] || '';
    const tabContent = linkEl.closest('.tab-content') || document;

    const targetContainer = tabContent.querySelector(
        `.viewer-container[${containerDataAttr}="${CSS.escape(name)}"]`
    );
    if (!targetContainer) {
        console.warn(`Container for "${name}" not found`);
        return null;
    }

    const viewer = targetContainer.querySelector(viewerSelector);
    if (!viewer) return null;

    const allLinks = queryLinks(tabContent, linkSelector);
    const multi = allLinks.length > 1;

    setActiveLink(allLinks, linkEl);
    showSingleContainer(`.viewer-container[${containerDataAttr}]`, targetContainer, tabContent);

    return { name, tabContent, targetContainer, viewer, multi };
}

// =============================================================================
// HTML SNIPPET BUILDERS
// =============================================================================

/** Общий селектор placeholder-элементов во viewer-контейнерах */
const PLACEHOLDER_SELECTOR = '.png-loading-placeholder, .converting-placeholder, .viewer-loading, .image-loading-placeholder, .track-loading-placeholder';

/**
 * Генерирует HTML spinner-placeholder с заданным CSS-классом.
 * @param {string} className - CSS-класс контейнера
 * @returns {string} HTML строка
 */
export function spinnerPlaceholderHtml(className) {
    return `<div class="${className}"><span class="spinner"></span><span>Загрузка...</span></div>`;
}

// Вспомогательные inline SVG иконки для файловых ссылок в табах
function _inlineSvg(iconHref) {
    return `<svg width="24" height="24" style="display:inline-block;vertical-align:middle"><use href="${iconHref}"></use></svg>`;
}
export const DOWNLOAD_SVG = _inlineSvg(CONSTANTS.ICONS.DOWNLOAD);
export const EXTERNAL_SVG = _inlineSvg(CONSTANTS.ICONS.EXTERNAL);

/**
 * Подготавливает общие данные для генерации HTML файловой ссылки:
 * валидация URL, экранирование имён, размер файла.
 * @param {object} file - Объект файла из архива
 * @returns {{validation: object, fullName: string, displayName: string, fileSize: number,
 *            downloadUrl: string, escapedFullName: string, escapedDisplayName: string, escapedUrl: string}}
 */
export function prepareFileLink(file) {
    const validation = validateResourceUrl(file.download_url, 'default');
    const fullName = String(file.name ?? '');
    const displayName = fullName.replace(/^.*[\\/]/, '');
    return {
        validation,
        fullName,
        displayName,
        fileSize: file.size,
        downloadUrl: file.download_url,
        escapedFullName: escapeAttribute(fullName),
        escapedDisplayName: escapeHtml(displayName),
        escapedUrl: validation.valid ? validation.url.replace(/"/g, '&quot;') : '',
    };
}

/**
 * Подготавливает общий контекст для buildPdfLinkHtml и buildImageLinkHtml:
 * вычисляет activeClass, windowName и safeSizeTitle.
 * @param {object} prep - Результат prepareFileLink
 * @param {{fileIndex: number, shifr: string|number, dopShifr: string}} ctx
 * @returns {object}
 */
export function prepareLinkContext(prep, { fileIndex, shifr, dopShifr }) {
    const { escapedUrl, escapedFullName, escapedDisplayName, fullName, fileSize } = prep;
    return {
        escapedUrl, escapedFullName, escapedDisplayName, fullName, fileSize,
        activeClass: fileIndex === 0 ? ' active' : '',
        windowName: FileUtils.makeWindowName(shifr, dopShifr, fullName),
        safeSizeTitle: escapeAttribute(FileUtils.formatSizeShort(fileSize)),
    };
}

/**
 * Генерирует HTML блока viewer-контейнеров для одного таба (PDF или Изображения).
 * Инкапсулирует общий скелет: wrapper -> forEach(file) -> validateResourceUrl ->
 * if valid: buildValidHtml / else: viewer-error контейнер.
 *
 * @param {object[]} files - Массив файлов
 * @param {object} options
 * @param {string} options.wrapperClass - CSS-класс обёртки
 * @param {string} options.dataAttrName - data-атрибут контейнера
 * @param {string} options.archiveName - Имя архива (для data-archive-name)
 * @param {function} options.buildValidHtml - callback(file, {validation, escapedName, escapedArchive, displayStyle}) -> HTML
 * @returns {string} HTML строка блока
 */
export function buildViewersBlockHtml(files, { wrapperClass, dataAttrName, archiveName, buildValidHtml }) {
    let html = `<div class="${wrapperClass}">`;
    files.forEach((file, index) => {
        const validation = validateResourceUrl(file.download_url, 'default');
        const displayStyle = index === 0 ? '' : ' style="display:none"';
        const escapedName = escapeAttribute(file.name);
        const escapedArchive = escapeAttribute(archiveName);

        if (validation.valid) {
            html += buildValidHtml(file, { validation, escapedName, escapedArchive, displayStyle });
        } else {
            console.warn(`Invalid URL for "${file.name}": ${validation.reason}`);
            html += `<div class="viewer-container" ${dataAttrName}="${escapedName}" data-archive-name="${escapedArchive}"${displayStyle}>
                <div class="viewer-error">${CONSTANTS.MESSAGES.INVALID_URL}</div>
            </div>`;
        }
    });
    html += '</div>';
    return html;
}

/**
 * Генерирует HTML ссылки файла категории «Прочее».
 * @param {object} prep - Результат prepareFileLink
 * @returns {string}
 */
export function buildOtherLinkHtml(prep) {
    const { escapedUrl, escapedDisplayName, fileSize } = prep;
    const sizeStr = FileUtils.formatSizeShort(fileSize);
    return `<a href="${escapedUrl}" target="_blank" rel="noopener noreferrer" title="${sizeStr}">${escapedDisplayName}${FileUtils.formatSizeInMb(fileSize)}</a>`;
}

// =============================================================================
// VIEWER LIFECYCLE
// =============================================================================

/** Маппинг стадии подготовки кеша на сообщение из CONSTANTS.MESSAGES */
const STAGE_TO_MESSAGE = {
    starting:   CONSTANTS.MESSAGES.CACHE_STAGE_STARTING,
    extracting: CONSTANTS.MESSAGES.CACHE_STAGE_EXTRACTING,
    converting: CONSTANTS.MESSAGES.CACHE_STAGE_CONVERTING
};

/**
 * Показывает сообщение загрузки с деталями стадии.
 * @param {HTMLElement} container - контейнер viewer'а
 * @param {string} message - основное сообщение
 * @param {string} [detail] - дополнительные детали (например "PDF 3/10")
 */
export function showLoadingMessage(container, message, detail = '') {
    const placeholder = container.querySelector(PLACEHOLDER_SELECTOR);
    if (!placeholder) return;

    placeholder.className = 'viewer-loading';
    const detailHtml = detail ? `<div class="loading-detail">${escapeHtml(detail)}</div>` : '';
    placeholder.innerHTML = `
        <span class="spinner"></span>
        <span class="loading-message">${escapeHtml(message)}<span class="loading-dots"></span></span>
        ${detailHtml}
    `;
}

/**
 * Показывает ошибку в placeholder контейнера.
 * @param {HTMLElement} container - контейнер viewer'а
 * @param {string} message - текст ошибки
 * @param {Function} [retryCallback] - callback для кнопки "Повторить"
 */
export function showViewerError(container, message, retryCallback) {
    const placeholder = container.querySelector(PLACEHOLDER_SELECTOR);
    if (!placeholder) return;

    // CSP: никаких inline onclick. Только addEventListener.
    placeholder.innerHTML = `
        <span class="error-icon">⚠️</span>
        <span class="error-message">${escapeHtml(message)}</span>
        <button class="retry-button" type="button">Повторить</button>
    `;

    const btn = placeholder.querySelector('.retry-button');
    if (btn && retryCallback) {
        btn.addEventListener('click', () => {
            btn.disabled = true;
            retryCallback();
        });
    }
}

/**
 * Активирует iframe viewer: устанавливает src, удаляет placeholder, пересчитывает высоту.
 * @param {HTMLElement} container - Контейнер viewer'а
 * @param {HTMLIFrameElement} iframe - iframe элемент
 * @param {string} src - URL для загрузки в iframe
 * @param {string} [placeholderSelector] - CSS-селектор placeholder'а для удаления
 */
export function activateViewerIframe(container, iframe, src, placeholderSelector) {
    iframe.src = src;
    delete iframe.dataset.pendingUrl;
    iframe.style.display = '';

    const selector = placeholderSelector || PLACEHOLDER_SELECTOR;
    const placeholder = container.querySelector(selector);
    if (placeholder) placeholder.remove();

    scheduleHeightAdjustOnLoad(iframe);
}

// =============================================================================
// ASYNC LOADING
// =============================================================================

/**
 * Единый метод для resolve файла с ожиданием готовности.
 * Polling с экспоненциальным backoff и показом прогресса.
 *
 * @param {HTMLElement} container - контейнер viewer'а для показа прогресса
 * @param {string} archiveName - имя архива без расширения
 * @param {string} kind - тип контента ('pdf', 'image', 'track', 'all_tracks')
 * @param {string} zipPath - путь к файлу внутри архива (пустой для all_tracks)
 * @param {Object} [options]
 * @param {number} [options.maxAttempts]
 * @returns {Promise<Object>} - результат resolve (status: 'ready', url/png_dir/pages)
 * @throws {Error} - при timeout, error, not_prepared, not_found
 */
export async function resolveAndWait(container, archiveName, kind, zipPath, { maxAttempts = CONSTANTS.LIMITS.RESOLVE_MAX_ATTEMPTS } = {}) {
    let attempts = 0;
    let delay = CONSTANTS.TIMING.RESOLVE_INITIAL_DELAY;

    while (attempts < maxAttempts) {
        let res;
        try {
            res = await cacheWarmService.resolveFile(archiveName, { kind, path: zipPath });
        } catch (e) {
            // Сетевая ошибка — транзиентная, повторяем с backoff
            await sleep(delay);
            delay = Math.min(delay * CONSTANTS.TIMING.RESOLVE_BACKOFF_MULTIPLIER, CONSTANTS.TIMING.RESOLVE_MAX_DELAY);
            attempts++;
            continue;
        }

        if (res.status === 'ready') return res;
        if (res.status === 'preparing' && res.png_dir) return res;
        if (res.status === 'error') throw new Error(res.message || 'Ошибка подготовки кеша');
        if (res.status === 'not_prepared' || res.status === 'not_found') {
            throw new Error(res.status === 'not_prepared' ? 'Кеш не подготовлен' : 'Файл не найден');
        }

        // preparing — показываем прогресс
        const message = STAGE_TO_MESSAGE[res.stage] || 'Подготовка...';
        showLoadingMessage(container, message, res.detail);

        await sleep(delay);
        delay = Math.min(delay * CONSTANTS.TIMING.RESOLVE_BACKOFF_MULTIPLIER, CONSTANTS.TIMING.RESOLVE_MAX_DELAY);
        attempts++;
    }

    throw new Error(CONSTANTS.MESSAGES.CACHE_TIMEOUT);
}

