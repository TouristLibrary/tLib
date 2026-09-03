// Категории файлов и расширения

import { deepFreeze } from '../utils/freeze.js';

/**
 * Категории файлов (порядок определяет сортировку: 1 - PDF, 2 - Треки, 3 - Изображения, 4 - Прочее)
 */
export const FILE_CATEGORIES = deepFreeze({
    PDF: 1,
    GPS_TRACKS: 2,
    IMAGES: 3,
    OTHER: 4
});

/**
 * Расширения файлов
 * Примечание: GPS_TRACKS удален - используется serverConfig.extensions.gpsTracks
 * Примечание: IMAGES удален - используется serverConfig.extensions.images
 */
export const FILE_EXTENSIONS = deepFreeze({
    PDF: ['pdf']
});

/**
 * Типы файлов отчетов (используются для определения типа архива)
 */
export const FILE_TYPES = deepFreeze({
    PDF: 'pdf',
    ZIP: 'zip'
});

/**
 * MIME типы
 * Примечание: удален - используется serverConfig.mimeTypes
 */
