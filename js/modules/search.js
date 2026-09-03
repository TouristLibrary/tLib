// Version 4.4 - 25.03.2026
// Модуль поисковой системы с серверным поиском и ленивой пагинацией по скроллу
// Описание: Выполняет поиск через серверный API /api/search и сохраняет результаты в appState.
//           Первый запрос загружает первую страницу (SEARCH_PAGE_SIZE записей) и публикует событие search:results-changed.
//           При наличии следующих страниц сохраняет состояние пагинации и эмитит search:autoload-started.
//           Последующие чанки загружаются по одному через loadNextPage(), вызываемый из UI по событию скролла.
//           Содержит защиту от race condition: активный поиск идентифицируется currentSearchId, устаревшие загрузки отменяются.

import { CONSTANTS } from '../config/constants.js';
import { errorHandler } from '../core/errorHandler.js';
import { appState } from '../core/appState.js';
import { fetchApiJson } from '../utils/fetchUtils.js';

/**
 * Основной класс поисковой системы
 */
class SearchEngine {
    constructor() {
        this.currentSearchId = 0;
        this._pagination = null; // {formData, total, pageSize, offset, searchId}
        this._loading = false;   // защита от параллельных вызовов loadNextPage
    }

    /**
     * Генерирует событие завершения фоновой загрузки страниц
     * @param {number} searchId - Идентификатор поиска
     * @param {number} offset - Количество загруженных записей
     * @param {number} total - Общее количество записей
     * @param {'cancelled'|'error'|'completed'} reason - Причина завершения
     */
    _emitAutoloadFinished(searchId, offset, total, reason) {
        appState.emit('search:autoload-finished', { searchId, loaded: offset, total, reason });
    }

    /**
     * Клонирует FormData и добавляет дополнительные параметры
     * @param {FormData} formData - Исходные данные формы
     * @param {object} extraParams - Дополнительные параметры для добавления
     * @returns {FormData} Клонированный FormData с дополнительными параметрами
     */
    _cloneFormData(formData, extraParams = {}) {
        const clone = new FormData();
        for (const [key, value] of formData.entries()) {
            clone.append(key, value);
        }
        for (const [key, value] of Object.entries(extraParams)) {
            clone.append(key, value);
        }
        return clone;
    }

    /**
     * Выполняет поиск по данным формы через серверный API с автоматической пагинацией
     * @param {FormData} formData - Данные формы поиска
     * @param {boolean} autoLoadAll - Автоматически загружать все страницы (по умолчанию true)
     * @returns {Promise<Array>} Результаты поиска
     */
    async search(formData) {
        try {
            this.currentSearchId++;
            const searchId = this.currentSearchId;
            this._pagination = null;
            this._loading = false;
            
            console.log(`Начат новый поиск #${searchId}`);
            
            const firstPageData = this._cloneFormData(formData, {
                limit: CONSTANTS.LIMITS.SEARCH_PAGE_SIZE,
                offset: 0
            });
            
            const result = await fetchApiJson(CONSTANTS.API.SEARCH, {
                method: 'POST',
                body: firstPageData
            });
            
            console.log(`Первая страница загружена: ${result.count} из ${result.total || result.count}`);
            
            appState.setSearchResults(result.data, result.total || result.count);
            
            if (result.has_more) {
                this._pagination = {
                    formData,
                    total: result.total,
                    pageSize: CONSTANTS.LIMITS.SEARCH_PAGE_SIZE,
                    offset: result.count,
                    searchId
                };
                appState.emit('search:autoload-started', {
                    searchId,
                    loaded: result.count,
                    total: result.total
                });
            }
            
            return result.data;

        } catch (error) {
            errorHandler.handle(error);
            throw error;
        }
    }

    /**
     * Возвращает true, если есть незагруженные страницы для текущего поиска
     * @returns {boolean}
     */
    get hasMorePages() {
        return this._pagination !== null && this._pagination.offset < this._pagination.total;
    }

    /**
     * Загружает следующий чанк результатов. Вызывается из UI по событию скролла.
     * Ничего не делает, если страниц больше нет или уже идёт загрузка.
     * @returns {Promise<Array|null>} Загруженные строки или null
     */
    async loadNextPage() {
        if (!this.hasMorePages || this._loading) return null;

        const { formData, total, pageSize, offset, searchId } = this._pagination;

        if (searchId !== this.currentSearchId) {
            this._pagination = null;
            return null;
        }

        this._loading = true;
        try {
            const pageData = this._cloneFormData(formData, { limit: pageSize, offset });
            const result = await fetchApiJson(CONSTANTS.API.SEARCH + '?page=1', {
                method: 'POST',
                body: pageData
            });

            if (searchId !== this.currentSearchId) {
                this._pagination = null;
                return null;
            }

            if (!result.data.length) {
                this._emitAutoloadFinished(searchId, offset, total, 'completed');
                this._pagination = null;
                return null;
            }

            appState.appendSearchResults(result.data);
            const newOffset = offset + result.count;
            this._pagination.offset = newOffset;

            appState.emit('search:results-appended', {
                searchId,
                rows: result.data,
                loaded: newOffset,
                total
            });

            console.log(`Загружено ${newOffset} из ${total} записей (поиск #${searchId})`);

            if (newOffset >= total) {
                this._emitAutoloadFinished(searchId, newOffset, total, 'completed');
                this._pagination = null;
            }

            return result.data;

        } catch (error) {
            console.error('Ошибка при загрузке страницы:', error);
            this._emitAutoloadFinished(searchId, offset, total, 'error');
            this._pagination = null;
            return null;
        } finally {
            this._loading = false;
        }
    }

}

// Создаем единственный экземпляр поисковой системы
export const searchEngine = new SearchEngine();
