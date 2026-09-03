// Version 1.1 - 14.06.2026
// Описание: Сервис для работы с архивами.
//   - openOriginal() — открывает оригинальный файл из архива в новой вкладке.
// 1.1: удалён неиспользуемый импорт NetworkError (класс удалён из errorHandler).

import { CONSTANTS } from '../config/constants.js';
import { errorHandler } from '../core/errorHandler.js';

/**
 * Открывает оригинальный файл в новой вкладке.
 * Сначала делает HEAD-запрос для проверки доступности, затем открывает файл нативно.
 *
 * @param {string} archiveName - имя архива без расширения
 * @param {string} filepath - путь к файлу внутри архива
 */
export async function openOriginal(archiveName, filepath) {
    const url = `${CONSTANTS.API.ARCHIVE_BASE}/${encodeURIComponent(archiveName)}/file/${encodeURIComponent(filepath)}?original=1`;
    try {
        const head = await fetch(url, { method: 'HEAD' });
        if (head.ok) {
            window.open(url, '_blank');
        } else if (head.status === 503) {
            // data/ недоступна — делаем GET для получения JSON с download_url
            const resp = await fetch(url);
            const data = await resp.json();
            errorHandler.handle(data.message || 'Исходные данные недоступны');
            if (data.download_url) {
                window.open(data.download_url, '_blank');
            }
        } else {
            errorHandler.handle('Не удалось открыть оригинальный файл');
        }
    } catch (e) {
        console.error('Error opening original:', e);
        errorHandler.handle('Ошибка при открытии файла');
    }
}
