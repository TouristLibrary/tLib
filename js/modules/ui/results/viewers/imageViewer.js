// Version 1.1 - 20.02.2026 - Зона 2: hash state вынесен в viewStateManager.js
// Описание: Viewer-стратегия для изображений. Реализует контракт viewer-стратегии:
//   buildFileListHtml, buildViewersHtml, setupHandlers, selectLink, autoLoad,
//   applyHashState, getHashStateOnTabSwitch.

import { CONSTANTS, TAB_TOKENS, TAB_IDS } from '../../../../config/constants.js';
import { escapeAttribute, validateResourceUrl } from '../../../../utils/sanitize.js';
import { replaceUrlHash } from './viewStateManager.js';
import {
    findLinkByDataset,
    setupClickDelegates, scheduleHeightAdjust, scheduleHeightAdjustOnLoad,
    prepareFileLink, prepareLinkContext, buildViewersBlockHtml, spinnerPlaceholderHtml,
    showLoadingMessage, showViewerError,
    prepareViewerSwitch, resolveAndWait,
    EXTERNAL_SVG,
} from './viewerHelpers.js';
import { openOriginal } from '../../../../services/archiveService.js';

// =============================================================================
// HTML BUILDERS
// =============================================================================

/**
 * Генерирует HTML ссылки изображения для таба изображений.
 * @param {object} prep - Результат prepareFileLink
 * @param {{fileIndex: number, shifr: string|number, dopShifr: string, localFileName: string}} ctx
 * @returns {string}
 */
function buildImageLinkHtml(prep, { fileIndex, shifr, dopShifr, localFileName }) {
    const { escapedUrl, escapedFullName, escapedDisplayName, activeClass, safeSizeTitle } =
        prepareLinkContext(prep, { fileIndex, shifr, dopShifr });
    return `<a href="#" class="tab-link${activeClass}" data-image-url="${escapedUrl}" data-image-name="${escapedFullName}" title="${safeSizeTitle}">${escapedDisplayName}</a> <a href="#" class="external-link-icon open-original-link" data-archive-name="${escapeAttribute(localFileName || '')}" data-original-path="${escapedFullName}" title="Открыть ${safeSizeTitle}">${EXTERNAL_SVG}</a>`;
}

// =============================================================================
// STRATEGY EXPORTS
// =============================================================================

/**
 * Генерирует HTML содержимого file-list для файлов изображений.
 * @param {object[]} files
 * @param {{shifr: string|number, dopShifr: string, localFileName: string}} ctx
 * @returns {string}
 */
export function buildFileListHtml(files, ctx) {
    const links = files.map((file, fileIndex) => {
        const prep = prepareFileLink(file);
        if (!prep.validation.valid) {
            console.warn(`Invalid download_url for file "${prep.fullName}": ${prep.validation.reason}`);
            return `<span class="disabled" title="${CONSTANTS.MESSAGES.INVALID_URL}">${prep.escapedDisplayName}</span>`;
        }
        return buildImageLinkHtml(prep, { ...ctx, fileIndex });
    });
    return links.join('; ');
}

/**
 * Генерирует HTML viewer-контейнеров для изображений.
 * @param {object[]} files
 * @param {{archiveName: string, isPlainMode: boolean}} options
 * @returns {string}
 */
export function buildViewersHtml(files, { archiveName, isPlainMode }) {
    return buildViewersBlockHtml(files, {
        wrapperClass: 'image-viewers-wrapper',
        dataAttrName: 'data-image-name',
        archiveName,
        buildValidHtml: (file, { validation, escapedName, escapedArchive, displayStyle }) => {
            const shouldLazyLoad = !isPlainMode;
            const imgStyle = shouldLazyLoad ? ' style="display:none"' : '';
            // Для lazy изображений: не устанавливаем src (браузер интерпретирует пустой src как URL страницы!)
            const srcAttr = shouldLazyLoad ? '' : `src="${validation.url}"`;
            const pendingAttr = shouldLazyLoad ? ` data-pending-url="${escapeAttribute(validation.url)}"` : '';
            return `<div class="viewer-container" data-image-name="${escapedName}" data-archive-name="${escapedArchive}"${displayStyle}>
                ${shouldLazyLoad ? spinnerPlaceholderHtml('image-loading-placeholder') : ''}
                <img ${srcAttr} class="viewer image-viewer" alt="Просмотр изображения ${escapeAttribute(file.name)}"${pendingAttr}${imgStyle} />
            </div>`;
        },
    });
}

/**
 * Навешивает обработчики кликов для изображений:
 * - ссылки в file-list
 * - ссылки открытия оригинала (.open-original-link)
 */
export function setupHandlers() {
    setupClickDelegates('.tab-link[data-image-url]', (el) => selectLink(el));

    // Ссылки открытия оригинала (с обработкой 503 ошибок)
    document.querySelectorAll('.open-original-link').forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            const archiveName = this.dataset.archiveName || '';
            const filepath = this.dataset.originalPath || '';
            if (archiveName && filepath) {
                openOriginal(archiveName, filepath);
            }
        });
    });

    // Навигация стрелками между изображениями
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;

        const activeTab = document.querySelector('.tab-content.active');
        if (!activeTab || activeTab.id !== TAB_IDS.IMG) return;

        const links = [...activeTab.querySelectorAll('.tab-link[data-image-url]')];
        if (links.length <= 1) return;

        const activeIndex = links.findIndex(l => l.classList.contains('active'));
        if (activeIndex === -1) return;

        const nextIndex = e.key === 'ArrowRight' ? activeIndex + 1 : activeIndex - 1;
        if (nextIndex < 0 || nextIndex >= links.length) return;

        e.preventDefault();
        selectLink(links[nextIndex]);
    });
}

/**
 * Выбирает изображение: переключает viewer, active class и обновляет hash URL.
 * @param {HTMLElement} linkEl
 * @param {{forceLoad?: boolean}} [options]
 */
export async function selectLink(linkEl, options = {}) {
    const ctx = prepareViewerSwitch(linkEl, {
        nameDataset: 'imageName',
        containerDataAttr: 'data-image-name',
        viewerSelector: '.image-viewer',
        linkSelector: '.tab-link[data-image-url]',
    });
    if (!ctx) return;
    const { name: imageName, targetContainer, viewer: imageViewer, multi } = ctx;

    scheduleHeightAdjust();

    const file = multi ? String(imageName || '') : undefined;
    replaceUrlHash({ tab: TAB_TOKENS.IMG, file });

    const currentSrc = imageViewer.getAttribute('src') || '';
    const isImageLoaded = currentSrc && (currentSrc.includes('/api/') || currentSrc.includes('/cache/'));

    if (isImageLoaded && !options.forceLoad) return;

    try {
        const archiveName = targetContainer.dataset.archiveName || '';
        if (!archiveName) throw new Error('Archive name not found');

        showLoadingMessage(targetContainer, 'Загрузка...');

        const result = await resolveAndWait(targetContainer, archiveName, 'image', imageName);

        if (result.status === 'ready' && result.url) {
            const imageValidation = validateResourceUrl(result.url, 'default');
            if (!imageValidation.valid) {
                console.warn(`Invalid image URL: ${imageValidation.reason}`);
                return;
            }

            imageViewer.src = imageValidation.url;
            imageViewer.style.display = '';

            const placeholder = targetContainer.querySelector('.image-loading-placeholder, .viewer-loading');
            if (placeholder) placeholder.remove();

            scheduleHeightAdjustOnLoad(imageViewer);
        }
    } catch (error) {
        console.error(`Error loading image ${imageName}:`, error);
        showViewerError(targetContainer, error.message || 'Не удалось загрузить изображение');
    }
}

/**
 * Автоматически загружает первое изображение при активации таба.
 * @param {HTMLElement} tabContent
 */
export function autoLoad(tabContent) {
    if (!tabContent) return;

    const firstLink = tabContent.querySelector('.tab-link[data-image-url].active')
                   || tabContent.querySelector('.tab-link[data-image-url]');
    if (firstLink) {
        selectLink(firstLink);
    }
}

/**
 * Применяет hash state для таба изображений: выбирает нужную ссылку.
 * @param {HTMLElement} tabContent
 * @param {{file?: string}} state
 */
export function applyHashState(tabContent, state) {
    const imageLinks = tabContent.querySelectorAll('.tab-link[data-image-url]');
    if (!imageLinks.length) return;

    const targetImageLink = findLinkByDataset(
        imageLinks, 'imageName', state.file || '', tabContent, '.tab-link[data-image-url].active'
    );
    selectLink(targetImageLink);
}

/**
 * Возвращает hash state при переключении на таб изображений.
 * @returns {{tab: string}}
 */
export function getHashStateOnTabSwitch() {
    return { tab: TAB_TOKENS.IMG };
}
