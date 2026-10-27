// Описание: Viewer-стратегия для 3D-карты треков (kartoid.com iframe). Реализует контракт viewer-стратегии:
//   buildFileListHtml, buildViewersHtml, autoLoad, getHashStateOnTabSwitch.
//   Треки — одной ссылкой на /cache/, как у nakarte.me: все треки отчёта — архивом (getGpsAllTracksUrl), один — сам трек.
//   Файл скачивает сервер Kartoid; ссылка и поля отчёта — в hash адреса. Кеш готовит resolve при открытии вкладки;
//   общего архива нет — первый трек, как у «Треков»; не подготовил и его — фрейм открывается с fail=<код> (FAIL_CODES),
//   и причину показывает Kartoid.

import { CONSTANTS, TAB_TOKENS } from '../../../../config/constants.js';
import { escapeAttribute } from '../../../../utils/sanitize.js';
import { FileUtils } from '../../../fileUtils.js';
import { DataFormatter } from '../../dataFormatter.js';
import { activateViewerIframe, resolveAndWait, spinnerPlaceholderHtml, EXTERNAL_SVG } from './viewerHelpers.js';
import { getGpsAllTracksUrl } from './trackViewer.js';

/** Маппинг параметров Kartoid -> колонки отчёта */
const FIELDS = [
    ['route', 'Маршрут'], ['area', 'РайонОбщий'], ['region', 'Район'], ['type', 'Тип'], ['boat', 'ТипСудна'],
    ['grade', 'КатегорияС'], ['gradeTo', 'КатегорияПо'], ['year', 'Год'], ['month', 'МесяцС'], ['monthTo', 'МесяцПо'],
    ['author', 'Автор'], ['city', 'Город'], ['note', 'Комментарии'], ['uploaded', 'ДатаВремяЗагрузки'], ['uploader', 'ЗагрузилИмя'],
];

/** Статус resolve -> код fail фрейма Kartoid (missing, broken, timeout; иначе offline) */
const FAIL_CODES = { not_found: 'missing', not_prepared: 'missing', error: 'broken', timeout: 'timeout' };

/**
 * Первый трек, который открывает Kartoid (GPX, KML, KMZ): запасной вариант, если общего архива треков нет.
 * @param {object[]} files
 * @returns {object|undefined}
 */
function firstKartoidTrack(files) {
    return files.find(file =>
        CONSTANTS.FILE_EXTENSIONS.KARTOID_TRACKS.includes(String(file.name ?? '').toLowerCase().split('.').pop().trim()));
}

/**
 * Файл треков в /cache/ и параметры resolve для него.
 * @param {object[]} files
 * @param {string} archiveName
 * @returns {{url: string, kind: string, path: string}}
 */
function cacheTarget(files, archiveName) {
    const allTracksUrl = getGpsAllTracksUrl(files, archiveName);
    if (allTracksUrl) return { url: allTracksUrl, kind: 'all_tracks', path: '' };
    return { url: files[0].download_url, kind: 'track', path: files[0].name };
}

/**
 * Формирует URL Kartoid: файл треков, шифр и поля отчёта, ссылка на отчёт.
 * @param {string} baseUrl
 * @param {object[]} files
 * @param {{archiveName: string, row: object}} ctx
 * @returns {string}
 */
function buildKartoidUrl(baseUrl, files, { archiveName, row }) {
    const params = new URLSearchParams();
    params.set('p', `${window.location.origin}${cacheTarget(files, archiveName).url}`);
    const { linkText, shareUrl } = DataFormatter.buildRowDownloadInfo(row);
    params.set('code', linkText);
    for (const [key, column] of FIELDS) {
        const value = String(row[column] ?? '').trim();
        if (value) params.set(key, value);
    }
    params.set('from', shareUrl);
    params.set('fromName', `Открыть отчёт на ${window.location.hostname}`);
    return `${baseUrl}#${params.toString()}`;
}

/**
 * Генерирует HTML содержимого file-list: ссылку на редактор Kartoid. Ссылка неактивна, пока кеш не готов.
 * @param {object[]} files
 * @param {{shifr: string|number, dopShifr: string, archiveName: string, row: object}} ctx
 * @returns {string}
 */
export function buildFileListHtml(files, ctx) {
    const url = buildKartoidUrl(CONSTANTS.EXTERNAL_URLS.KARTOID_MAP, files, ctx);
    const windowName = FileUtils.makeWindowName(ctx.shifr, ctx.dopShifr, 'kartoid');
    return `<a href="${escapeAttribute(url)}" target="${escapeAttribute(windowName)}" rel="noopener" data-pending-track="kartoid">Открыть в редакторе Картоид ${EXTERNAL_SVG}</a>`;
}

/**
 * Генерирует HTML viewer-контейнера с kartoid.com iframe.
 * @param {object[]} files
 * @param {{archiveName: string, row: object}} ctx
 * @returns {string}
 */
export function buildViewersHtml(files, ctx) {
    const url = buildKartoidUrl(CONSTANTS.EXTERNAL_URLS.KARTOID_TRACK, files, ctx);
    const { kind, path } = cacheTarget(files, ctx.archiveName);
    // Общий архив может не собраться или быть вытеснен — тогда первый трек, как у «Треков» (dropAllTracks)
    const first = kind === 'all_tracks' ? firstKartoidTrack(files) : null;
    const fallback = first ? ` data-fallback-path="${escapeAttribute(first.name)}"` +
        ` data-fallback-url="${escapeAttribute(buildKartoidUrl(CONSTANTS.EXTERNAL_URLS.KARTOID_TRACK, [first], ctx))}"` +
        ` data-fallback-map="${escapeAttribute(buildKartoidUrl(CONSTANTS.EXTERNAL_URLS.KARTOID_MAP, [first], ctx))}"` : '';

    return `<div class="viewer-container" data-archive-name="${escapeAttribute(ctx.archiveName)}" data-kind="${kind}" data-path="${escapeAttribute(path)}"${fallback}>
        ${spinnerPlaceholderHtml('track-loading-placeholder')}
        <iframe referrerpolicy="no-referrer" class="viewer" title="Просмотр треков на kartoid.com" data-pending-url="${escapeAttribute(url)}" style="display:none"></iframe>
    </div>`;
}

/**
 * Готовит кеш и загружает карту при активации таба.
 * @param {HTMLElement} tabContent
 */
export async function autoLoad(tabContent) {
    const iframe = tabContent?.querySelector('iframe[data-pending-url]');
    if (!iframe || iframe.dataset.loading) return;
    iframe.dataset.loading = '1';
    const container = iframe.parentElement;
    const { archiveName, kind, path, fallbackPath, fallbackUrl, fallbackMap } = container.dataset;
    const link = tabContent.querySelector('a[data-pending-track="kartoid"]');
    try {
        let url = iframe.dataset.pendingUrl;
        try {
            await resolveAndWait(container, archiveName, kind, path);
        } catch (error) {
            // Сервер окончательно сказал, что общего архива треков нет (не собрался, вытеснен): первый трек
            if (!fallbackUrl || (error.status !== 'not_found' && error.status !== 'error')) throw error;
            console.warn(`Kartoid: no all-tracks archive for ${archiveName}, showing the first track:`, error);
            await resolveAndWait(container, archiveName, 'track', fallbackPath);
            url = fallbackUrl;
            if (link) link.href = fallbackMap;
        }
        activateViewerIframe(container, iframe, url);
        link?.removeAttribute('data-pending-track');
    } catch (error) {
        // Причину показывает и учитывает Kartoid: во фрейме — текст и совет, что делать
        console.error(`Error loading Kartoid map for ${archiveName}:`, error);
        activateViewerIframe(container, iframe, `${iframe.dataset.pendingUrl}&fail=${FAIL_CODES[error.status] || 'offline'}`);
    } finally {
        delete iframe.dataset.loading;
    }
}

/**
 * Возвращает hash state при переключении на таб карты.
 * @returns {{tab: string}}
 */
export function getHashStateOnTabSwitch() {
    return { tab: TAB_TOKENS.MAP };
}
