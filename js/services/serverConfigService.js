// Version 1.5 - 15.06.2026
// Описание: Сервис для загрузки конфигурации сервера. Запрашивает GET /api/config при инициализации приложения
//           и сохраняет конфигурацию для использования клиентским кодом. Устраняет дублирование конфигурации
//           между config.py и js/config/. Включает критичные настройки:
//           - paths.localArchive (путь к архивам)
//           - extensions.gpsTracks (расширения GPS-треков)
//           - extensions.images (расширения изображений)
//           - specialValues.noDopShifr (значение "нет" для ДопШифр)
//           - mimeTypes (MIME типы файлов, ключи без точек)
//           При ошибке загрузки выбрасывает исключение - приложение останавливает инициализацию.
//           Перенесён из js/modules/ в слой js/services/.

import { API } from '../config/api.config.js';
import { fetchApiJson } from '../utils/fetchUtils.js';

/**
 * Хранилище загруженной конфигурации сервера
 * @type {Object|null}
 */
let serverConfig = null;

/**
 * Загружает конфигурацию с сервера
 * 
 * Выполняет GET запрос к /api/config и сохраняет результат.
 * При ошибке выбрасывает исключение, которое останавливает инициализацию приложения.
 * 
 * @throws {Error} При ошибке HTTP запроса или невалидном ответе сервера
 * @returns {Promise<Object>} Загруженная конфигурация
 */
export async function loadServerConfig() {
    try {
        const json = await fetchApiJson(API.CONFIG);

        if (!json.data) {
            throw new Error('Конфигурация не содержит данных');
        }

        serverConfig = json.data;
        console.log('Конфигурация сервера загружена:', serverConfig);

        return serverConfig;
    } catch (error) {
        console.error('Не удалось загрузить конфигурацию сервера:', error);
        throw error;
    }
}

/**
 * Возвращает загруженную конфигурацию сервера
 * 
 * @throws {Error} Если конфигурация еще не загружена
 * @returns {Object} Объект конфигурации с полями paths, extensions, specialValues, mimeTypes
 */
export function getServerConfig() {
    if (!serverConfig) {
        throw new Error('Конфигурация не загружена. Вызовите loadServerConfig() перед использованием.');
    }
    return serverConfig;
}
