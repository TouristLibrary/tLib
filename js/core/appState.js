// Version 2.7 - 20.02.2026
// Управление состоянием приложения
// Описание: Singleton для централизованного хранения состояния: API, поиск, UI, справочные данные, события.
//           Генерация событий, сброс состояния.
//           Метод appendSearchResults() добавляет результаты без генерации события для инкрементальной подгрузки при пагинации.
//           DOM-кеширование перенесено в DOMUtils (modules/ui/utils.js).

/**
 * Центральное хранилище состояния приложения
 */
class AppState {
    static instance = null;

    constructor() {
        if (AppState.instance) {
            return AppState.instance;
        }
        AppState.instance = this;
        this.initializeState();
    }

    /**
     * Инициализирует начальное состояние приложения
     */
    initializeState() {
        this.state = {
            // Состояние готовности API
            api: {
                isReady: false
            },

            // Состояние поиска
            search: {
                isSearching: false,
                results: [],
                hasResults: false,
                total: 0  // Общее количество результатов для пагинации
            },

            // Состояние UI
            ui: {
                isInitialized: false
            },

            // Состояние событий
            events: {
                listeners: new Map() // Зарегистрированные слушатели событий
            },

            // Справочные данные
            reference: {
                categoryOrder: [] // Порядок категорий для сортировки (из kategoria_s_list)
            }
        };
    }

    /**
     * Устанавливает готовность серверного API
     * @param {boolean} isReady - Флаг готовности API
     */
    setDatabaseReady(isReady) {
        this.state.api.isReady = isReady;
        this.emit('databaseReady:changed', { ready: isReady });
    }

    /**
     * Устанавливает состояние поиска
     * @param {boolean} isSearching - Флаг активного поиска
     */
    setSearching(isSearching) {
        this.state.search.isSearching = isSearching;
        this.emit('search:status-changed', { isSearching });
    }

    /**
     * Устанавливает результаты поиска
     * @param {Array} results - Массив результатов поиска
     * @param {number} total - Общее количество результатов (для пагинации), если null - используется results.length
     */
    setSearchResults(results, total = null) {
        this.state.search.results = results;
        this.state.search.hasResults = results.length > 0;
        this.state.search.total = total !== null ? total : results.length;
        this.emit('search:results-changed', { 
            results, 
            total: this.state.search.total 
        });
    }

    /**
     * Очищает результаты поиска без генерации событий
     */
    clearSearchResults() {
        this.state.search.results = [];
        this.state.search.hasResults = false;
    }

    /**
     * Добавляет результаты поиска к существующим без генерации события
     * Используется для инкрементальной подгрузки страниц при пагинации
     * @param {Array} results - Массив результатов для добавления
     */
    appendSearchResults(results) {
        this.state.search.results.push(...results);
        this.state.search.hasResults = this.state.search.results.length > 0;
        // НЕ вызываем emit - избегаем полной перерисовки таблицы
    }

    /**
     * Получает результаты поиска
     * @returns {Array} Массив результатов
     */
    getSearchResults() {
        return this.state.search.results;
    }

    /**
     * Устанавливает порядок категорий для сортировки
     * @param {Array} categories - Массив категорий в порядке возрастания
     */
    setCategoryOrder(categories) {
        this.state.reference.categoryOrder = categories || [];
    }

    /**
     * Подписывается на событие
     * @param {string} eventName - Название события
     * @param {Function} callback - Функция обратного вызова
     * @returns {Function} Функция для отписки
     */
    on(eventName, callback) {
        if (!this.state.events.listeners.has(eventName)) {
            this.state.events.listeners.set(eventName, new Set());
        }
        
        this.state.events.listeners.get(eventName).add(callback);
        
        // Возвращаем функцию для отписки
        return () => {
            const listeners = this.state.events.listeners.get(eventName);
            if (listeners) {
                listeners.delete(callback);
            }
        };
    }

    /**
     * Генерирует событие
     * @param {string} eventName - Название события
     * @param {*} data - Данные события
     */
    emit(eventName, data = null) {
        const listeners = this.state.events.listeners.get(eventName);
        if (listeners) {
            listeners.forEach(callback => {
                try {
                    callback(data);
                } catch (error) {
                    console.error(`Ошибка в слушателе события ${eventName}:`, error);
                }
            });
        }
    }

}



// Создаем единственный экземпляр состояния
export const appState = new AppState(); 