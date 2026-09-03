// Version 1.1 - 15.06.2026
// Описание: Утилиты для файлов: категоризация по расширению, форматирование размеров,
//           генерация ссылок nakarte.me, формирование имён окон браузера.
//           Выделено из archive.js (Version 2.12).

import { CONSTANTS } from '../config/constants.js';
import { validateResourceUrl } from '../utils/sanitize.js';
import { getServerConfig } from '../services/serverConfigService.js';

/**
 * Утилиты для файлов
 */
export class FileUtils {
    /**
     * Определяет категорию файла по расширению
     */
    static getFileCategory(filename) {
        const ext = filename.toLowerCase().split('.').pop().trim();
        
        if (CONSTANTS.FILE_EXTENSIONS.PDF.includes(ext)) {
            return CONSTANTS.FILE_CATEGORIES.PDF;
        }
        
        if (getServerConfig().extensions.gpsTracks.includes(ext)) {
            return CONSTANTS.FILE_CATEGORIES.GPS_TRACKS;
        }
        
        if (getServerConfig().extensions.images.includes(ext)) {
            return CONSTANTS.FILE_CATEGORIES.IMAGES;
        }
        
        return CONSTANTS.FILE_CATEGORIES.OTHER;
    }

    /**
     * Конвертирует байты в строку "X Mb" или "<1 Mb"
     * @param {number} bytes
     * @returns {string}
     */
    static _toMbString(bytes) {
        const mb = bytes / (1024 * 1024);
        return mb < 1 ? '<1 Mb' : `${mb.toFixed(0)} Mb`;
    }

    /**
     * Форматирует размер в мегабайтах со скобками (для download-атрибутов)
     * Для файлов меньше 1 МБ показывает (<1 Mb)
     */
    static formatSizeInMb(bytes) {
        if (!bytes) return '';
        return ` (${FileUtils._toMbString(bytes)})`;
    }

    /**
     * Форматирует размер в мегабайтах без скобок (для title-атрибутов)
     * Для файлов меньше 1 МБ показывает <1 Mb
     */
    static formatSizeShort(bytes) {
        if (!bytes) return '';
        return FileUtils._toMbString(bytes);
    }

    /**
     * Формирует ссылку на nakarte.me с треком
     * @param {string} trackUrl - URL файла GPS-трека (может быть относительным)
     * @param {boolean} forIframe - Если true, добавляет параметр min для минимизации интерфейса (по умолчанию false)
     * @returns {string} Ссылка для открытия трека на nakarte.me
     */
    static makeNakarteLink(trackUrl, forIframe = false) {
        const trackValidation = validateResourceUrl(trackUrl, CONSTANTS.VALIDATION_CONTEXTS.DEFAULT);
        if (!trackValidation.valid) {
            console.warn(`Invalid track URL in makeNakarteLink: ${trackValidation.reason}`);
            return CONSTANTS.EXTERNAL_URLS.NAKARTE_BASE;
        }
        
        let fullUrl = trackValidation.url;
        const encodedUrl = encodeURIComponent(fullUrl);
        const baseUrl = `${CONSTANTS.EXTERNAL_URLS.NAKARTE_TRACK_PREFIX}${encodedUrl}`;
        
        return forIframe ? `${baseUrl}${CONSTANTS.EXTERNAL_URLS.NAKARTE_IFRAME_PARAMS}` : baseUrl;
    }

    /**
     * Санитизирует строку для безопасного использования в атрибуте target окна браузера
     * @param {string} str - Входная строка
     * @returns {string} Строка с пробелами→подчеркивание и удалёнными спецсимволами
     */
    static sanitizeWindowNamePart(str) {
        return str
            .replace(/\s+/g, '_')
            .replace(/[^\w\-_.]/g, '');
    }

    static makeWindowName(shifr, dopShifr, fileName) {
        let base = String(shifr);
        if (dopShifr && dopShifr.trim()) {
            base += `-${FileUtils.sanitizeWindowNamePart(dopShifr.trim())}`;
        }
        
        return `${base}_${FileUtils.sanitizeWindowNamePart(fileName)}`;
    }
}
