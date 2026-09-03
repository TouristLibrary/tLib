// Version 1.2 - 20.02.2026 - Рефакторинг: дублированная IIFE вынесена в getGpsAllTracksUrl()
// Описание: Viewer-стратегия для GPS-треков (nakarte.me iframe). Реализует контракт viewer-стратегии:
//   buildFileListHtml, buildViewersHtml, setupHandlers, autoLoad,
//   applyHashState, getHashStateOnTabSwitch.

import { CONSTANTS, TAB_TOKENS, TAB_IDS } from '../../../../config/constants.js';
import { escapeAttribute, validateResourceUrl } from '../../../../utils/sanitize.js';
import { FileUtils } from '../../../fileUtils.js';
import { replaceUrlHash } from './viewStateManager.js';
import {
    findLinkByDataset,
    setActiveLink, queryLinks, scheduleHeightAdjust,
    spinnerPlaceholderHtml, prepareFileLink,
    showViewerError, activateViewerIframe, resolveAndWait,
    DOWNLOAD_SVG, EXTERNAL_SVG,
} from './viewerHelpers.js';

// =============================================================================
// INTERNAL HTML BUILDERS
// =============================================================================

/**
 * Генерирует HTML viewer-контейнера с nakarte.me iframe для одного трека или набора треков.
 * @param {object} options
 * @param {string} options.trackUrl
 * @param {string} options.trackName
 * @param {string} options.archiveName
 * @param {boolean} options.isPlainMode
 * @param {string} [options.extraClass]
 * @param {string} options.title
 * @returns {string}
 */
function buildNakarteViewerHtml({ trackUrl, trackName, archiveName, isPlainMode, extraClass = '', title }) {
    const nakarteUrl = FileUtils.makeNakarteLink(trackUrl, true);
    const validation = validateResourceUrl(nakarteUrl, CONSTANTS.VALIDATION_CONTEXTS.IFRAME_NAKARTE);

    if (!validation.valid) {
        console.warn(`Invalid nakarte URL for ${trackName}: ${validation.reason}`);
        return `<div class="viewer-container" data-track-name="${escapeAttribute(trackName)}">
            <div class="viewer-error">${CONSTANTS.MESSAGES.INVALID_URL}</div>
        </div>`;
    }

    const escapedUrl = validation.url.replace(/"/g, '&quot;');
    const shouldLazyLoad = !isPlainMode;
    const iframeSrc = shouldLazyLoad ? 'about:blank' : escapedUrl;
    const pendingAttr = shouldLazyLoad ? ` data-pending-url="${escapedUrl}"` : '';

    return `<div class="viewer-container${extraClass}" data-track-name="${escapeAttribute(trackName)}" data-archive-name="${escapeAttribute(archiveName)}">
        ${spinnerPlaceholderHtml('track-loading-placeholder')}
        <iframe referrerpolicy="no-referrer" src="${iframeSrc}" class="viewer nakarte-viewer" title="${escapeAttribute(title)}"${pendingAttr} style="display:none"></iframe>
    </div>`;
}

/**
 * Возвращает URL all-tracks для архива, или null если треков <= 1.
 * @param {object[]} files
 * @returns {string|null}
 */
function getGpsAllTracksUrl(files, archiveName) {
    if (files.length <= 1 || !archiveName) return null;
    return `/cache/${archiveName}/${archiveName}-geo.zip`;
}

/**
 * Генерирует HTML ссылки GPS-трека.
 * @param {object} prep - Результат prepareFileLink
 * @param {{fileIndex: number, filesCount: number, shifr: string|number, dopShifr: string}} ctx
 * @returns {string}
 */
function buildGpsTrackLinkHtml(prep, { fileIndex, filesCount, shifr, dopShifr }) {
    const { escapedUrl, escapedFullName, escapedDisplayName, fullName, fileSize, downloadUrl } = prep;
    const nakarteUrl = FileUtils.makeNakarteLink(downloadUrl);
    const windowName = FileUtils.makeWindowName(shifr, dopShifr, fullName);
    const sizeTitle = FileUtils.formatSizeShort(fileSize);

    if (filesCount === 1) {
        const activeClass = fileIndex === 0 ? ' active' : '';
        return `<a href="#" class="tab-link${activeClass}" data-track-url="${escapedUrl}" data-track-name="${escapedFullName}" title="${sizeTitle}">${escapedDisplayName}</a> <a href="${escapedUrl}" class="track-download-icon" download title="Скачать трек">${DOWNLOAD_SVG}</a> <a href="${nakarteUrl}" class="external-link-icon" target="${escapeAttribute(windowName)}" title="Открыть в новой вкладке">${EXTERNAL_SVG}</a>`;
    }
    return `<a href="#" class="tab-link" data-track-url="${escapedUrl}" data-track-name="${escapedFullName}" title="${sizeTitle}">${escapedDisplayName}</a> <a href="${nakarteUrl}" class="external-link-icon" target="${escapeAttribute(windowName)}" title="Открыть в новой вкладке">${EXTERNAL_SVG}</a>`;
}

/**
 * Загружает трек в iframe через resolveAndWait.
 * @param {HTMLElement} container
 * @param {function} [adjustFn]
 */
async function loadTrackViewer(container) {
    if (!container) return;

    const iframe = container.querySelector('.nakarte-viewer');
    const pendingUrl = iframe?.dataset?.pendingUrl;

    if (!iframe || !pendingUrl) return;

    const archiveName = container.dataset.archiveName || '';
    if (!archiveName) {
        // Fallback: загружаем напрямую без resolve (для backward compatibility)
        activateViewerIframe(container, iframe, pendingUrl);
        return;
    }

    const trackName = container.dataset.trackName || '';
    const kind = trackName === 'all-tracks' ? 'all_tracks' : 'track';
    const zipPath = kind === 'all_tracks' ? '' : trackName;

    try {
        await resolveAndWait(container, archiveName, kind, zipPath);
        activateViewerIframe(container, iframe, pendingUrl);
    } catch (error) {
        console.error(`Error loading track ${trackName}:`, error);
        showViewerError(container, error.message || 'Не удалось загрузить трек');
    }
}

// =============================================================================
// STRATEGY EXPORTS
// =============================================================================

/**
 * Генерирует HTML содержимого file-list для GPS-треков.
 * Включает специальную ссылку "Все треки" если треков > 1.
 * @param {object[]} files
 * @param {{shifr: string|number, dopShifr: string, archiveName?: string}} ctx
 * @returns {string}
 */
export function buildFileListHtml(files, { shifr, dopShifr, archiveName }) {
    const gpsAllTracksUrl = getGpsAllTracksUrl(files, archiveName);

    let html = '';

    // Ссылка "Все треки" в начале списка
    if (files.length > 1 && gpsAllTracksUrl) {
        const nakarteUrl = FileUtils.makeNakarteLink(gpsAllTracksUrl);
        const escapedAllTracksUrl = gpsAllTracksUrl.replace(/"/g, '&quot;');
        const escapedNakarteUrl = nakarteUrl.replace(/"/g, '&quot;');
        const windowName = FileUtils.makeWindowName(shifr, dopShifr, 'all-tracks');
        const totalSize = files.reduce((sum, file) => sum + (file.size || 0), 0);
        const allTracksSizeStr = FileUtils.formatSizeShort(totalSize);

        html += `<a href="#" class="tab-link active" data-track-url="${escapedAllTracksUrl}" title="${allTracksSizeStr}">Все треки</a> `;
        html += `<a href="${escapedAllTracksUrl}" class="track-download-icon" download title="Скачать все треки">${DOWNLOAD_SVG}</a> `;
        html += `<a href="${escapedNakarteUrl}" class="external-link-icon" target="${escapeAttribute(windowName)}" title="Открыть все треки на nakarte.me">${EXTERNAL_SVG}</a>`;
        html += '; ';
    }

    const fileLinks = files.map((file, fileIndex) => {
        const prep = prepareFileLink(file);
        if (!prep.validation.valid) {
            console.warn(`Invalid download_url for track "${prep.fullName}": ${prep.validation.reason}`);
            return `<span class="disabled" title="${CONSTANTS.MESSAGES.INVALID_URL}">${prep.escapedDisplayName}</span>`;
        }
        return buildGpsTrackLinkHtml(prep, { fileIndex, filesCount: files.length, shifr, dopShifr });
    });

    html += fileLinks.join('; ');
    return html;
}

/**
 * Генерирует HTML viewer-контейнеров для GPS-треков.
 * @param {object[]} files
 * @param {{archiveName: string, isPlainMode: boolean}} options
 * @returns {string}
 */
export function buildViewersHtml(files, { archiveName, isPlainMode }) {
    const gpsAllTracksUrl = getGpsAllTracksUrl(files, archiveName);

    let html = '<div class="track-viewers-wrapper">';

    if (files.length > 1 && gpsAllTracksUrl) {
        html += buildNakarteViewerHtml({
            trackUrl: gpsAllTracksUrl,
            trackName: 'all-tracks',
            archiveName,
            isPlainMode,
            title: 'Просмотр всех треков на nakarte.me',
        });
    }

    files.forEach((file, index) => {
        const validation = validateResourceUrl(file.download_url, 'default');
        if (!validation.valid) {
            console.warn(`Invalid track URL: ${validation.reason}`);
            return;
        }
        const isVisible = files.length === 1 && index === 0;
        const trackName = String(file.name ?? '');
        html += buildNakarteViewerHtml({
            trackUrl: validation.url,
            trackName,
            archiveName,
            isPlainMode,
            extraClass: isVisible ? '' : ' track-offscreen',
            title: `Просмотр трека ${trackName} на nakarte.me`,
        });
    });

    html += '</div>';
    return html;
}

/**
 * Навешивает обработчики кликов для GPS-треков.
 */
export function setupHandlers() {
    const trackLinks = document.querySelectorAll('.tab-link[data-track-url]');

    trackLinks.forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();

            const trackName = this.dataset?.trackName || 'all-tracks';
            const tabContent = this.closest('.tab-content') || document;

            // Скрываем все треки (offscreen)
            tabContent.querySelectorAll('.viewer-container[data-track-name]').forEach(container => {
                container.classList.add('track-offscreen');
            });

            // Показываем нужный трек
            const targetContainer = tabContent.querySelector(
                `.viewer-container[data-track-name="${CSS.escape(trackName)}"]`
            );
            if (targetContainer) {
                targetContainer.classList.remove('track-offscreen');
                loadTrackViewer(targetContainer);
            } else {
                console.warn(`Container for track "${trackName}" not found`);
            }

            setActiveLink(queryLinks(tabContent, '.tab-link[data-track-url]'), this);

            scheduleHeightAdjust();

            // Hash sync
            try {
                if (trackName === 'all-tracks') {
                    replaceUrlHash({ tab: TAB_TOKENS.GEO });
                } else {
                    const linksInTab = queryLinks(tabContent, '.tab-link[data-track-url][data-track-name]');
                    const multi = linksInTab.length > 1;
                    const file = multi ? String(trackName || '') : undefined;
                    replaceUrlHash({ tab: TAB_TOKENS.GEO, file });
                }
            } catch (err) {
                console.warn('trackViewer: не удалось обновить hash для трека:', err);
            }
        });
    });

    // Навигация стрелками между треками
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;

        const activeTab = document.querySelector('.tab-content.active');
        if (!activeTab || activeTab.id !== TAB_IDS.GEO) return;

        const links = [...activeTab.querySelectorAll('.tab-link[data-track-url]')];
        if (links.length <= 1) return;

        const activeIndex = links.findIndex(l => l.classList.contains('active'));
        if (activeIndex === -1) return;

        const nextIndex = e.key === 'ArrowRight' ? activeIndex + 1 : activeIndex - 1;
        if (nextIndex < 0 || nextIndex >= links.length) return;

        e.preventDefault();
        links[nextIndex].click();
    });
}

/**
 * Автоматически загружает все треки при активации таба.
 * @param {HTMLElement} tabContent
 */
export function autoLoad(tabContent) {
    if (!tabContent) return;

    tabContent.querySelectorAll('.viewer-container[data-track-name]').forEach(container => {
        loadTrackViewer(container);
    });
}

/**
 * Применяет hash state для таба треков: выбирает нужную ссылку.
 * @param {HTMLElement} tabContent
 * @param {{file?: string}} state
 */
export function applyHashState(tabContent, state) {
    const trackLinks = tabContent.querySelectorAll('.tab-link[data-track-url]');
    if (!trackLinks.length) return;

    const namedTrackLinks = tabContent.querySelectorAll('.tab-link[data-track-url][data-track-name]');
    const targetTrackLink = findLinkByDataset(
        namedTrackLinks, 'trackName', state.file || '', tabContent,
        '.tab-link[data-track-url].active', trackLinks
    );
    if (targetTrackLink) targetTrackLink.click();
}

/**
 * Возвращает hash state при переключении на таб треков.
 * @returns {{tab: string}}
 */
export function getHashStateOnTabSwitch() {
    return { tab: TAB_TOKENS.GEO };
}
