// Version 2.2 - 20.02.2026
// Описание: Сервис для подготовки кеша архивов (eager caching v2).
//           prepareCache() - fire-and-forget запуск подготовки кеша при клике на таб.
//           resolveFile() - единый resolve для всех типов контента (pdf, image, track, all_tracks).

import { API } from '../config/api.config.js';
import { fetchJson } from '../utils/fetchUtils.js';

/**
 * Сервис для подготовки и resolve кеша
 */
class CacheWarmService {
    constructor() {
        /**
         * Отслеживание запущенных prepare
         * @type {Map<string, Promise>}
         * @private
         */
        this._warmingInProgress = new Map();
    }
    
    /**
     * Запускает подготовку кеша архива (fire-and-forget)
     * @param {string} archiveName - имя архива без расширения
     * @returns {Promise<Object>} - результат prepare
     */
    async prepareCache(archiveName) {
        // Dedup по archiveName
        const key = archiveName;
        
        if (this._warmingInProgress.has(key)) {
            console.log(`CacheWarmService: prepare already in progress for ${archiveName}`);
            return this._warmingInProgress.get(key);
        }
        
        const promise = fetchJson(`${API.CACHE_BASE}/${encodeURIComponent(archiveName)}/prepare`, { method: 'POST' })
            .catch(e => {
                console.error(`CacheWarmService: prepare error for ${archiveName}:`, e);
                return { status: 'error' };
            });
        
        this._warmingInProgress.set(key, promise);
        
        try {
            const result = await promise;
            console.log(`CacheWarmService: prepare response for ${archiveName}:`, result.status);
            return result;
        } finally {
            this._warmingInProgress.delete(key);
        }
    }
    
    /**
     * Resolve файла из кеша (единый метод для всех типов контента)
     * @param {string} archiveName - имя архива без расширения
     * @param {Object} options - параметры resolve
     * @param {string} options.kind - тип контента ('pdf', 'image', 'track', 'all_tracks')
     * @param {string} [options.path=''] - путь к файлу внутри архива (пустой для all_tracks)
     * @returns {Promise<Object>} - результат resolve
     */
    async resolveFile(archiveName, { kind, path = '' }) {
        try {
            return await fetchJson(
                `${API.CACHE_BASE}/${encodeURIComponent(archiveName)}/resolve`,
                { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ kind, path }) }
            );
        } catch (error) {
            console.error(`CacheWarmService: resolve error for ${archiveName}:`, error);
            return { status: 'error', message: error.message };
        }
    }
    
}



// Экспортируем singleton
export const cacheWarmService = new CacheWarmService();
