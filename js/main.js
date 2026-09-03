// Version 2.13 - 15.06.2026 12:00:00 GMT
// Главный файл приложения TlibWebApp
// Описание: Инициализирует приложение при загрузке DOM и координирует работу модулей (UI, поиск, архивы, редиректы).
//           Управляет жизненным циклом через класс TlibWebApp: загрузка конфигурации сервера, инициализация UI,
//           подключение к API, загрузка справочных списков, настройка обработчиков событий и обработка формы поиска.
//           Рендер результатов и состояние кнопки поиска синхронизируются через события appState.
//           Фоновая пагинация результатов отображается через индикатор прогресса и инкрементальное добавление строк
//           (события search:autoload-started / search:results-appended / search:autoload-finished).
//           Поддержка истории браузера: каждый запрос сохраняется в URL через History API, работают кнопки назад/вперёд,
//           URL обновляется в два этапа: pushState на полный формат при начале поиска, затем replaceState на компактный при 1 результате.
//           При загрузке страницы в футер выводится общее количество отчетов в базе данных.
//           Справочники загружаются в фоне через ReferenceListsService с проверкой версии (без блокировки UI).
//           Конфигурация загружается с сервера через /api/config при инициализации.
//           При ошибке инициализации показывается кнопка "Попробовать снова" вместо мёртвой страницы.

// ВАЖНО: При изменении импортов (прямых или транзитивных) обновите
// список <link rel="modulepreload"> в index.html

import { CONSTANTS, DEBUG_MODE } from './config/constants.js';
import { errorHandler } from './core/errorHandler.js';
import { appState } from './core/appState.js';
import { searchEngine } from './modules/search.js';
import { UIController, ResultsRenderer, ButtonManager, FormManager } from './modules/ui.js';
import { ReferenceListsService } from './services/referenceListsService.js';
import { RedirectManager, URLRedirectHandler, buildFormDataFromParams } from './modules/redirect.js';
import { loadServerConfig } from './services/serverConfigService.js';
import { initSidebarAuth } from './modules/sidebarAuth.js';
import { fetchApiJson } from './utils/fetchUtils.js';

// Отключение debug-логов в production
if (!DEBUG_MODE) {
    console.log = console.debug = () => {};
}

/**
 * Основной класс приложения
 */
class TlibWebApp {
    constructor() {
        this.isInitialized = false;
        this.initializationPromise = null;
        // Флаг навигации по истории браузера (кнопки назад/вперёд)
        // Когда true, поиск не добавляет новую запись в историю
        this.isNavigatingHistory = false;
    }

    /**
     * Инициализирует приложение
     */
    async initialize() {
        if (this.initializationPromise) {
            return this.initializationPromise;
        }

        this.initializationPromise = this.performInitialization();
        return this.initializationPromise;
    }

    /**
     * Выполняет инициализацию компонентов
     */
    async performInitialization() {
        try {
            console.log('Начало инициализации TlibWebApp...');

            // Загружаем конфигурацию сервера (критично для работы приложения)
            console.log('Загрузка конфигурации с сервера...');
            await loadServerConfig();
            console.log('Конфигурация успешно загружена');

            // Скрываем loading-плейсхолдер сразу после успешного получения конфига
            this._hideAppLoader();

            await this.initializeUI();
            await this.initializeDatabase();
            this.setupEventHandlers();
            await initSidebarAuth();
            await this.initializeRedirects();

            this.isInitialized = true;
            console.log('TlibWebApp успешно инициализирован');

        } catch (error) {
            errorHandler.handle(error);
            console.error('Ошибка инициализации приложения:', error);
            throw error;
        }
    }

    /**
     * Скрывает loading-плейсхолдер (#app-loader внутри #results)
     */
    _hideAppLoader() {
        const loader = document.getElementById('app-loader');
        if (loader) {
            loader.remove();
        }
    }

    /**
     * Показывает блок ошибки с кнопкой "Попробовать снова" в #results
     * @param {string} [message]
     */
    showRetryUI(message) {
        this._hideAppLoader();
        const resultsEl = document.querySelector(CONSTANTS.SELECTORS.RESULTS);
        if (!resultsEl) return;

        const errorMessage = message || CONSTANTS.MESSAGES.INIT_RETRY;
        const buttonLabel = CONSTANTS.MESSAGES.INIT_RETRY_BUTTON;

        const block = document.createElement('div');
        block.id = 'app-init-error';
        block.className = 'app-init-error';
        block.innerHTML = `
            <div class="error-message">${errorMessage}</div>
            <button class="retry-btn" type="button">${buttonLabel}</button>
        `;

        block.querySelector('.retry-btn').addEventListener('click', () => {
            this.retryInitialization();
        });

        resultsEl.innerHTML = '';
        resultsEl.appendChild(block);
    }

    /**
     * Убирает блок ошибки с кнопкой повтора из #results
     */
    hideRetryUI() {
        const block = document.getElementById('app-init-error');
        if (block) block.remove();
    }

    /**
     * Сбрасывает состояние инициализации и запускает её заново
     */
    retryInitialization() {
        this.isInitialized = false;
        this.initializationPromise = null;

        // Показываем спиннер повторно
        const resultsEl = document.querySelector(CONSTANTS.SELECTORS.RESULTS);
        if (resultsEl) {
            resultsEl.innerHTML = `
                <div id="app-loader" style="display:flex;flex-direction:column;align-items:center;justify-content:center;padding:60px 20px;gap:16px;text-align:center;">
                    <div style="width:40px;height:40px;border:4px solid rgba(0,0,0,0.1);border-top-color:#3073b7;border-radius:50%;animation:spin .8s linear infinite;"></div>
                    <div style="font-size:16px;color:#666;">${CONSTANTS.MESSAGES.INIT_LOADING}</div>
                </div>
            `;
        }

        errorHandler.clearError();

        this.initialize().catch(error => {
            console.error('Повторная инициализация провалилась:', error);
            this.showRetryUI();
        });
    }

    /**
     * Инициализирует UI
     */
    async initializeUI() {
        console.log('Инициализация UI...');
        
        UIController.initialize();
        
        // Изначально кнопка неактивна до загрузки базы
        ButtonManager.setSearchState('waiting');

        // Загружаем справочники в фоне (не блокирует инициализацию UI)
        try {
            ReferenceListsService.initialize();
        } catch (error) {
            console.warn('Не удалось запустить загрузку справочников:', error);
        }

        console.log('UI инициализирован');
    }

    /**
     * Инициализирует готовность API
     */
    async initializeDatabase() {
        console.log('Инициализация подключения к API...');
        
        try {
            // Устанавливаем готовность API (проверка доступности будет при первом запросе)
            appState.setDatabaseReady(true);
            ButtonManager.setSearchState('ready');
            
            console.log('API готов к работе');
        } catch (error) {
            // При ошибке показываем сообщение и оставляем кнопку неактивной
            ButtonManager.setSearchState('waiting');
            console.error('Ошибка подключения к API:', error);
            throw error;
        }
    }

    /**
     * Инициализирует систему редиректов
     */
    async initializeRedirects() {
        console.log('Инициализация системы редиректов...');
        
        try {
            await RedirectManager.initialize();
            console.log('Система редиректов инициализирована');
        } catch (error) {
            console.warn('Ошибка инициализации редиректов (не критично):', error);
        }
    }

    /**
     * Настраивает обработчики событий
     */
    setupEventHandlers() {
        console.log('Настройка обработчиков событий...');

        this.setupSearchFormHandler();
        this.setupSortDirectionHandler();
        this.setupStateEventHandlers();

        console.log('Обработчики событий настроены');
    }

    /**
     * Настраивает кнопку переключения направления сортировки
     */
    setupSortDirectionHandler() {
        const sortBtn = document.querySelector('#sortDirectionBtn');
        const sortOrderInput = document.querySelector('#sortOrderInput');
        if (!sortBtn || !sortOrderInput) return;

        sortBtn.addEventListener('click', () => {
            const isDesc = sortOrderInput.value === 'desc';
            const newOrder = isDesc ? 'asc' : 'desc';
            sortOrderInput.value = newOrder;
            const icon = sortBtn.querySelector('use');
            if (icon) {
                icon.setAttribute('href', isDesc ? CONSTANTS.ICONS.SORT_DOWN : CONSTANTS.ICONS.SORT_UP);
            }
        });
    }

    /**
     * Настраивает обработчик поиска
     */
    setupSearchFormHandler() {
        const searchForm = document.querySelector(CONSTANTS.SELECTORS.SEARCH_FORM);
        
        if (searchForm) {
            searchForm.addEventListener('submit', async (event) => {
                await this.handleSearchSubmit(event);
            });

            const routeTextarea = searchForm.querySelector('textarea[name="Маршрут"]');
            if (routeTextarea) {
                routeTextarea.addEventListener('keydown', (event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                        event.preventDefault();
                        searchForm.requestSubmit();
                    }
                });
            }
        } else {
            console.error('Форма поиска не найдена');
        }
    }

    /**
     * Настраивает обработчики состояния
     */
    setupStateEventHandlers() {
        // Защита от race conditions: UI игнорирует события фоновой догрузки неактуального поиска
        let activeAutoloadSearchId = null;
        let scrollObserver = null;

        appState.on('search:status-changed', (data) => {
            // Если начался новый поиск — сбрасываем состояние фоновой догрузки и убираем старый индикатор
            if (data?.isSearching) {
                activeAutoloadSearchId = null;
                ResultsRenderer.hideLoadingIndicator();
                if (scrollObserver) { scrollObserver.disconnect(); scrollObserver = null; }
            }
            UIController.updateSearchState(data.isSearching);
        });

        appState.on('search:results-changed', async (data) => {
            // Сначала обновляем кнопку (до рендера, чтобы избежать race condition)
            // Используем total если есть (для пагинации), иначе длину массива
            const count = data.total || (Array.isArray(data.results) ? data.results.length : 0);
            ButtonManager.setSearchState('complete', count);
            // Затем рендерим результаты
            await ResultsRenderer.render(data.results);
        });

        // Ленивая пагинация: следующий чанк грузится только при скролле к индикатору
        appState.on('search:autoload-started', (data) => {
            if (!data || typeof data.searchId !== 'number') return;
            activeAutoloadSearchId = data.searchId;
            ResultsRenderer.showLoadingIndicator(data.loaded ?? 0, data.total ?? 0);

            if (scrollObserver) scrollObserver.disconnect();

            const sentinel = document.querySelector(CONSTANTS.SELECTORS.LOADING_INDICATOR);
            if (!sentinel) return;

            scrollObserver = new IntersectionObserver((entries) => {
                if (entries[0].isIntersecting && searchEngine.hasMorePages) {
                    searchEngine.loadNextPage();
                }
            }, { rootMargin: `0px 0px ${CONSTANTS.LIMITS.SEARCH_PREFETCH_PX}px 0px` });

            scrollObserver.observe(sentinel);
        });

        appState.on('search:results-appended', (data) => {
            if (!data || data.searchId !== activeAutoloadSearchId) return;
            const rows = Array.isArray(data.rows) ? data.rows : [];
            if (rows.length) {
                ResultsRenderer.appendRowsToTable(rows);
            }
            ResultsRenderer.updateLoadingIndicator(data.loaded ?? 0, data.total ?? 0);
        });

        appState.on('search:autoload-finished', (data) => {
            if (!data || data.searchId !== activeAutoloadSearchId) return;
            ResultsRenderer.hideLoadingIndicator();
            activeAutoloadSearchId = null;
            if (scrollObserver) { scrollObserver.disconnect(); scrollObserver = null; }
        });

        appState.on('databaseReady:changed', (data) => {
            console.log(`API сервера: ${data.ready ? 'готов' : 'не готов'}`);
        });

        // Автопоиск при навигации по истории / редиректе (эмитирует redirect.js)
        appState.on('redirect:auto-search', async ({ params }) => {
            try {
                FormManager.restoreFormFromParams(params);
                const formData = buildFormDataFromParams(params);
                appState.setSearching(true);
                try {
                    const results = await searchEngine.search(formData);
                    console.log(`redirect:auto-search завершён, найдено ${results.length} результатов`);
                } finally {
                    appState.setSearching(false);
                }
            } catch (error) {
                console.error('redirect:auto-search: ошибка:', error);
                appState.setSearching(false);
            }
        });

        // Сброс формы при навигации на начальное состояние (эмитирует redirect.js)
        appState.on('redirect:form-reset', () => {
            FormManager.resetToInitialState();
        });
    }

    /**
     * Обрабатывает отправку формы поиска
     * URL обновляется в два этапа: pushState при начале, replaceState на компактный если 1 результат
     */
    async handleSearchSubmit(event) {
        event.preventDefault();

        try {
            appState.setSearching(true);

            await new Promise(resolve => setTimeout(resolve, 0));

            errorHandler.clearError();

            const formData = new FormData(event.target);
            const usePushState = !this.isNavigatingHistory;
            
            // 1. Сразу обновляем URL на полный формат (pushState)
            // При навигации по истории (popstate) пропускаем — URL уже правильный
            if (usePushState) {
                URLRedirectHandler.updateBrowserUrlFullFormat(formData);
            }
            
            // 2. Выполняем поиск
            const results = await searchEngine.search(formData);

            console.log(`Поиск завершен. Найдено результатов: ${results.length}`);

            // 3. Если 1 результат — заменяем URL на компактный формат (replaceState)
            if (results.length === 1) {
                const row = results[0];
                URLRedirectHandler.updateBrowserUrl(
                    String(row["Шифр"]), 
                    row["ДопШифр"] || "", 
                    false // replaceState — заменяем текущую запись в истории
                );
            }
            
            // Сбрасываем флаг навигации
            this.isNavigatingHistory = false;

        } catch (error) {
            // URL остаётся в полном формате — пользователь видит, что искал
            console.error('Ошибка при поиске:', error);
            errorHandler.handle(error);
            this.isNavigatingHistory = false;
        } finally {
            appState.setSearching(false);
        }
    }
}

/**
 * Глобальный экземпляр приложения
 */
const tlibApp = new TlibWebApp();

/**
 * Загружает и отображает количество отчетов в футере
 */
async function loadReportsCount() {
    try {
        const data = await fetchApiJson(CONSTANTS.API.REPORTS_COUNT);

        if (data.count > 0) {
            const numberElement = document.querySelector(CONSTANTS.SELECTORS.REPORTS_NUMBER);
            if (numberElement) {
                numberElement.textContent = data.count.toLocaleString('ru-RU');
            }
        }
    } catch (error) {
        console.warn('Не удалось загрузить количество отчетов:', error);
    }
}

/**
 * Обработчик загрузки DOM
 */
document.addEventListener('DOMContentLoaded', async () => {
    console.log('DOM загружен, начинаем инициализацию приложения...');
    
    // Очистка cache-busting параметра из URL после перезагрузки
    const url = new URL(window.location.href);
    if (url.searchParams.has(CONSTANTS.SPECIAL_VALUES.CACHE_BUST_PARAM)) {
        url.searchParams.delete(CONSTANTS.SPECIAL_VALUES.CACHE_BUST_PARAM);
        window.history.replaceState({}, '', url.toString());
        console.log('Cache-busting параметр удален из URL');
    }
    
    try {
        await tlibApp.initialize();
        // Загружаем количество отчетов для футера (не блокирует основную инициализацию)
        loadReportsCount();
    } catch (error) {
        console.error('Критическая ошибка инициализации:', error);
        tlibApp.showRetryUI();
    }
});
