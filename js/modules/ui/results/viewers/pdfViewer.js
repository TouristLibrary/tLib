// Version 1.2 - 20.02.2026 - selectLink: boilerplate заменён на prepareViewerSwitch
// Описание: Viewer-стратегия для PDF (PNG viewer). Реализует контракт viewer-стратегии:
//   buildFileListHtml, buildViewersHtml, setupHandlers, selectLink, autoLoad,
//   applyHashState, getHashStateOnTabSwitch.

import { CONSTANTS, TAB_IDS, TAB_TOKENS } from '../../../../config/constants.js';
import { escapeAttribute } from '../../../../utils/sanitize.js';
import {
    parseViewStateFromHash, replaceUrlHash,
    setupPdfPageSyncListener, getSavedPdfPage,
} from './viewStateManager.js';
import {
    setActiveLink, setupClickDelegates, findLinkByDataset,
    safeFocusElement, scheduleHeightAdjust, adjustActiveViewerHeight,
    prepareFileLink, prepareLinkContext, buildViewersBlockHtml,
    showViewerError,
    activateViewerIframe,
    EXTERNAL_SVG, prepareViewerSwitch,
} from './viewerHelpers.js';

// =============================================================================
// MODULE STATE
// =============================================================================

// =============================================================================
// INTERNAL HELPERS
// =============================================================================

/**
 * Детерминированно вычисляет png_dir из archiveName + pdfName.
 * Зеркалит Python get_png_dir_path: data.cache/{archive}/{parent}/{stem}-png.
 * @param {string} archiveName
 * @param {string} pdfName - путь к PDF внутри архива (может содержать слеши)
 * @returns {string}
 */
function computePngDir(archiveName, pdfName) {
    const lastSlash = pdfName.lastIndexOf('/');
    const parent = lastSlash >= 0 ? pdfName.substring(0, lastSlash) : '';
    const fileName = lastSlash >= 0 ? pdfName.substring(lastSlash + 1) : pdfName;
    const dotIdx = fileName.lastIndexOf('.');
    const stem = dotIdx >= 0 ? fileName.substring(0, dotIdx) : fileName;
    const relDir = parent ? `${parent}/${stem}-png` : `${stem}-png`;
    return `${archiveName}/${relDir}`;
}

/**
 * Формирует URL для png-viewer с параметрами директории, страницы и общего числа страниц.
 * @param {string} pngDir
 * @param {number|undefined} page
 * @param {number} [pagesTotal]
 * @returns {string}
 */
function buildPngViewerUrl(pngDir, page, pagesTotal) {
    try {
        const baseUrl = CONSTANTS.API.PNG_VIEWER;
        const params = new URLSearchParams();
        params.set('dir', pngDir);
        if (typeof page === 'number' && Number.isFinite(page) && page >= 1) {
            params.set('page', page);
        }
        if (typeof pagesTotal === 'number' && pagesTotal > 0) {
            params.set('total', pagesTotal);
        }
        return `${baseUrl}#${params.toString()}`;
    } catch (error) {
        console.warn('buildPngViewerUrl: не удалось создать URL:', error);
        return `${CONSTANTS.API.PNG_VIEWER}#dir=${encodeURIComponent(pngDir)}`;
    }
}

/**
 * Отправляет команду смены страницы в png-viewer через postMessage.
 * @param {HTMLIFrameElement} iframe
 * @param {number} pageNumber
 */
function setPngViewerPage(iframe, pageNumber) {
    if (!iframe || !iframe.contentWindow) return;
    try {
        iframe.contentWindow.postMessage(
            { type: CONSTANTS.POSTMESSAGE_TYPES.GOTO_PAGE, pageNumber },
            window.location.origin
        );
    } catch (error) {
        console.warn('setPngViewerPage: ошибка отправки команды:', error);
    }
}

/**
 * Заменяет placeholder на iframe с png-viewer.
 * @param {HTMLElement} container
 * @param {string} pngDir
 * @param {number|undefined} page
 * @param {number} [pagesTotal]
 */
function replacePlaceholderWithViewer(container, pngDir, page, pagesTotal) {
    const iframe = container.querySelector('.png-viewer');
    if (iframe && pngDir) {
        activateViewerIframe(container, iframe, buildPngViewerUrl(pngDir, page, pagesTotal));
    } else {
        const placeholder = container.querySelector('.png-loading-placeholder, .converting-placeholder, .viewer-loading');
        if (placeholder) placeholder.remove();
    }
}

/**
 * Загружает PNG viewer для PDF немедленно, без resolve round-trip.
 * png_dir и pages_total берутся из data-атрибутов контейнера (заполняются при рендере).
 * Если страницы ещё не готовы — png-viewer сам ретраит /pages до их появления.
 * @param {HTMLElement} container
 * @param {string} pdfName
 */
function resolvePdfViewer(container, pdfName) {
    const iframe = container.querySelector('.png-viewer');
    if (iframe && iframe.src && !iframe.src.includes('about:blank') && iframe.src !== '') {
        return;
    }

    const archiveName = container.dataset.archiveName || '';
    if (!archiveName) {
        showViewerError(container, 'Archive name not found', () => resolvePdfViewer(container, pdfName));
        return;
    }

    const pngDir = container.dataset.pngDir || computePngDir(archiveName, pdfName);
    const pagesTotal = parseInt(container.dataset.pagesTotal, 10) || 0;
    const page = parseViewStateFromHash().p;

    replacePlaceholderWithViewer(container, pngDir, page, pagesTotal || undefined);
}

/**
 * Инициализирует все PNG viewer'ы на вкладке PDF.
 * Запускает resolve для всех PDF параллельно.
 */
async function initPdfTab() {
    const allPdfContainers = document.querySelectorAll('.viewer-container[data-pdf-name]');
    await Promise.all(
        Array.from(allPdfContainers).map(container =>
            resolvePdfViewer(container, container.dataset.pdfName)
        )
    );
}

/**
 * Устанавливает одноразовый обработчик load для фокуса на viewer.
 * @param {HTMLIFrameElement} viewerIframe
 */
function focusPdfViewerOnLoad(viewerIframe) {
    if (!viewerIframe) return;

    const onLoad = () => {
        viewerIframe.removeEventListener('load', onLoad);
        const pdfTabActive = document.querySelector(`.tab-button[data-tab="${TAB_IDS.PDF}"].active`);
        if (pdfTabActive) safeFocusElement(viewerIframe);
    };

    viewerIframe.addEventListener('load', onLoad);
}

/**
 * Фокусирует видимый PDF viewer для клавиатурной навигации.
 */
function focusPdfViewer() {
    const visibleContainer = document.querySelector(
        '.viewer-container[data-pdf-name]:not([style*="display: none"]):not([style*="display:none"])'
    );
    if (!visibleContainer) return;

    const pngViewer = visibleContainer.querySelector('.png-viewer');
    if (!pngViewer) return;

    if (pngViewer.contentDocument?.readyState === 'complete') {
        safeFocusElement(pngViewer);
    } else {
        focusPdfViewerOnLoad(pngViewer);
    }
}

// =============================================================================
// HTML BUILDERS
// =============================================================================

/**
 * Генерирует HTML ссылки PDF-файла для таба PDF.
 * @param {object} prep - Результат prepareFileLink
 * @param {{fileIndex: number, shifr: string|number, dopShifr: string, localFileName: string, isZip: boolean}} ctx
 * @returns {string}
 */
function buildPdfLinkHtml(prep, { fileIndex, shifr, dopShifr, localFileName, isZip }) {
    const { escapedUrl, escapedFullName, escapedDisplayName, windowName, activeClass, safeSizeTitle } =
        prepareLinkContext(prep, { fileIndex, shifr, dopShifr });

    if (isZip) {
        return `<a href="#" class="tab-link${activeClass}" data-pdf-url="${escapedUrl}" data-pdf-name="${escapedFullName}" title="${safeSizeTitle}">${escapedDisplayName}</a> <a href="#" class="external-link-icon open-original-link" data-archive-name="${escapeAttribute(localFileName || '')}" data-original-path="${escapedFullName}" title="Открыть ${safeSizeTitle}">${EXTERNAL_SVG}</a>`;
    }
    return `<a href="#" class="tab-link${activeClass}" data-pdf-url="${escapedUrl}" data-pdf-name="${escapedFullName}" title="${safeSizeTitle}">${escapedDisplayName}</a> <a href="${escapedUrl}" class="external-link-icon" target="${escapeAttribute(windowName)}" rel="noopener" title="Открыть ${safeSizeTitle}">${EXTERNAL_SVG}</a>`;
}

// =============================================================================
// STRATEGY EXPORTS
// =============================================================================

/**
 * Генерирует HTML содержимого file-list для PDF-файлов.
 * @param {object[]} files
 * @param {{fileIndex?: number, shifr: string|number, dopShifr: string, localFileName: string, isZip: boolean}} ctx
 * @returns {string}
 */
export function buildFileListHtml(files, ctx) {
    const links = files.map((file, fileIndex) => {
        const prep = prepareFileLink(file);
        if (!prep.validation.valid) {
            console.warn(`Invalid download_url for file "${prep.fullName}": ${prep.validation.reason}`);
            return `<span class="disabled" title="${CONSTANTS.MESSAGES.INVALID_URL}">${prep.escapedDisplayName}</span>`;
        }
        return buildPdfLinkHtml(prep, { ...ctx, fileIndex });
    });
    return links.join('; ');
}

/**
 * Генерирует HTML viewer-контейнеров для PDF-файлов (PNG viewer с placeholder).
 * @param {object[]} files
 * @param {{archiveName: string}} options
 * @returns {string}
 */
export function buildViewersHtml(files, { archiveName }) {
    return buildViewersBlockHtml(files, {
        wrapperClass: 'pdf-viewers-wrapper',
        dataAttrName: 'data-pdf-name',
        archiveName,
        buildValidHtml: (file, { escapedName, escapedArchive, displayStyle }) => {
            const pngDir = file.png_dir || computePngDir(archiveName, file.name);
            const ptAttr = file.pages ? ` data-pages-total="${file.pages}"` : '';
            return `<div class="viewer-container" data-pdf-name="${escapedName}" data-archive-name="${escapedArchive}" data-png-dir="${escapeAttribute(pngDir)}"${ptAttr}${displayStyle}>
                <iframe class="viewer png-viewer" allowfullscreen style="display:none"></iframe>
            </div>`;
        },
    });
}

/**
 * Навешивает обработчики кликов для PDF ссылок и запускает page-sync listener.
 */
export function setupHandlers() {
    setupClickDelegates('.tab-link[data-pdf-url]', (el) => selectLink(el));
    setupPdfPageSyncListener();
}

/**
 * Выбирает PDF ссылку: переключает viewer, active class и обновляет hash URL.
 * @param {HTMLElement} linkEl
 * @param {{preserveP?: number}} [options]
 */
export function selectLink(linkEl, options = {}) {
    const ctx = prepareViewerSwitch(linkEl, {
        nameDataset: 'pdfName',
        containerDataAttr: 'data-pdf-name',
        viewerSelector: '.png-viewer',
        linkSelector: '.tab-link[data-pdf-url]',
    });
    if (!ctx) return;
    const { name, viewer: pngViewer, multi } = ctx;

    // Определяем номер страницы для навигации
    let p;
    if (typeof options.preserveP === 'number') {
        p = options.preserveP;
    } else {
        p = getSavedPdfPage(name) || undefined;
    }

    const file = multi ? String(name || '') : undefined;
    replaceUrlHash({ tab: TAB_TOKENS.PDF, file, p });

    const isIframeLoaded = pngViewer.src && pngViewer.src !== '' && pngViewer.src !== 'about:blank';

    if (isIframeLoaded && typeof p === 'number' && p >= 1) {
        setPngViewerPage(pngViewer, p);
    }

    // Пересчёт высоты и фокус
    const focusViewer = () => {
        if (isIframeLoaded) {
            safeFocusElement(pngViewer);
        } else {
            focusPdfViewerOnLoad(pngViewer);
        }
    };
    requestAnimationFrame(() => {
        adjustActiveViewerHeight();
        requestAnimationFrame(focusViewer);
    });
}

/**
 * Автоматически загружает первый PDF при активации таба PDF.
 * @param {HTMLElement} tabContent
 */
export function autoLoad(tabContent) {
    if (!tabContent) return;

    initPdfTab().catch(err => {
        console.error('initPdfTab failed:', err);
    });

    const firstLink = tabContent.querySelector('.tab-link[data-pdf-url].active')
                   || tabContent.querySelector('.tab-link[data-pdf-url]');
    if (firstLink) {
        const pdfLinks = tabContent.querySelectorAll('.tab-link[data-pdf-url]');
        setActiveLink(pdfLinks, firstLink);

        const pdfName = firstLink.dataset?.pdfName || '';
        if (pdfName) {
            tabContent.querySelectorAll('.viewer-container[data-pdf-name]').forEach(container => {
                container.style.display = container.dataset.pdfName === pdfName ? '' : 'none';
            });
        }
    }
}

/**
 * Применяет hash state для PDF таба: выбирает нужную ссылку и страницу.
 * @param {HTMLElement} tabContent
 * @param {{file?: string, p?: number}} state
 */
export function applyHashState(tabContent, state) {
    const pdfLinks = tabContent.querySelectorAll('.tab-link[data-pdf-url]');
    if (!pdfLinks.length) return;

    const targetPdfLink = findLinkByDataset(
        pdfLinks, 'pdfName', state.file || '', tabContent, '.tab-link[data-pdf-url].active'
    );
    selectLink(targetPdfLink, { preserveP: state.p });
}

/**
 * Возвращает hash state при переключении на таб PDF.
 * Сохраняет номер страницы если ранее был активен PDF.
 * @param {string} targetTabId
 * @param {{tab?: string, p?: number}} prevState
 * @returns {{tab: string, file?: string, p?: number}}
 */
export function getHashStateOnTabSwitch(targetTabId, prevState) {
    const pdfLinks = document.querySelectorAll(`#${targetTabId} .tab-link[data-pdf-url]`);
    const preserveP = prevState.tab === 'pdf' ? prevState.p : undefined;

    let file;
    if (pdfLinks.length > 1) {
        const activePdfLink = document.querySelector(`#${targetTabId} .tab-link[data-pdf-url].active`);
        const linkEl = activePdfLink || (pdfLinks[0] || null);
        file = linkEl?.dataset?.pdfName || undefined;
    }

    return { tab: TAB_TOKENS.PDF, file, p: preserveP };
}

/**
 * Выполняет дополнительные действия после переключения на таб PDF (фокус viewer'а).
 * Вызывается из setupTabHandlers в single.js.
 */
export function onTabActivated() {
    requestAnimationFrame(() => focusPdfViewer());
}
