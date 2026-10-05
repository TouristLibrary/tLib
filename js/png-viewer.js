// Version 3.9 - 05.10.2026 09:33:00 GMT
// PNG Viewer - ESM модуль для просмотра PNG страниц
// 3.9: заглушка страницы, до которой дошёл observer, — со спиннером (класс is-busy):
//   «подготавливается», пока PNG нет на диске, «загружается» — пока готовый PNG скачивается;
//   окончательный отказ («не удалось загрузить») без спиннера
// 3.8: прыжок на неготовую страницу обрывает висящий long-poll со старым want (AbortController)
//   и сразу ставит опрос с новым; AbortError не ждёт 2 с. MIN_GAP 500 мс прежний
// 3.7: /pages — long-poll: пока страницы want нет, сервер сам держит запрос (до 1,5 с) и отвечает,
//   как только PNG появился. Пауза клиента между неудачными ожиданиями — PAGES_POLL_WAITING_MS 300 мс
//   вместо 1000: ждать на клиенте больше нечего. Логика опроса и MIN_GAP прежние
// 3.6: единый планировщик опроса /pages (_schedulePoll): один таймер, один запрос в полёте.
//   Текущей страницы нет на диске — опрос раз в PAGES_POLL_WAITING_MS, иначе раз в PAGES_POLL_IDLE_MS;
//   переход или скролл на неготовую страницу — опрос сразу (не чаще PAGES_POLL_MIN_GAP_MS), чтобы
//   сервер узнал новый want, не дожидаясь планового тика
// 3.5: PNG запрашивается только после появления в /pages (без 404 по заглушкам); повтор — до
//   IMAGE_RETRY_MAX раз, только на экране, только для 429/сети; после исчерпания счётчик сбрасывается,
//   возврат к странице даёт новый цикл
// 3.4: конвертация PDF, пока смотрят: /pages?want= (heartbeat + запрошенная страница), страницы
//   появляются не по порядку — список собирается и опрашивается по имени файла
// 
// АРХИТЕКТУРА:
// - ESM модуль с экспортом класса PngViewer
// - Режим работы: 'embedded' (iframe/интеграция)
// - Совместимость с single.js через postMessage API (pngviewer-page-change / pngviewer-goto-page)
// 
// ИНТЕГРАЦИЯ С ОСНОВНЫМ ПРИЛОЖЕНИЕМ:
// 1. В single.js импортировать: import { PngViewer } from '../../png-viewer.js';
// 2. Или использовать iframe: /png-viewer#dir=...&page=...
// 3. Слушать событие 'pngviewer-page-change' для синхронизации URL
// 
// API ENDPOINTS (настраиваются через options.apiBase):
// - GET {apiBase}/{path}/pages?want=N - список страниц в директории. Запрос — heartbeat
//   просмотра: сервер конвертирует PDF, только пока вьюер опрашивает /pages, и первой
//   рендерит страницу want (1-based), которую сейчас показывает вьюер, затем соседей с обеих
//   сторон, окно впереди и страницы позади неё. Пока PNG want нет на диске, сервер держит
//   запрос до 1,5 с (long-poll) и отвечает, как только страница готова
//
// СВЯЗЬ С PDF_TO_PNG_SERVICE:
// - PNG директории создаются автоматически при кешировании PDF
// - Паттерн именования: {stem}-png/ (например, report-png/)

import { API, POSTMESSAGE_TYPES } from './config/api.config.js';

// Configuration constants
const CONFIG = {
    // IntersectionObserver settings
    PRELOAD_MARGIN: '400px 0px',        // Буфер предзагрузки страниц (вверх/вниз, влево/вправо)
    INTERSECTION_THRESHOLD: 0.01,       // Порог видимости для загрузки (1%)
    
    // Timing settings
    SCROLL_THROTTLE_MS: 100,            // Throttle обновления при скролле (мс)
    SMOOTH_SCROLL_DELAY_MS: 600,        // Задержка после плавного скролла (мс)
    
    // Zoom settings
    DEFAULT_ZOOM: 1.0,                  // Умолчальный масштаб (1.0 = 100%)
    ZOOM_STEP: 0.1,                     // Шаг масштабирования (10%)
    MIN_ZOOM: 0.25,                     // Минимум 25%
    MAX_ZOOM: 3.0,                      // Максимум 300%
    
    // Rotation settings
    ROTATION_STEP: 90,                  // Шаг поворота в градусах

    // Повтор загрузки PNG, который уже есть на диске (429/сбой сети)
    IMAGE_RETRY_MAX: 3,                 // Повторов подряд; дальше — новый цикл при возврате к странице
    IMAGE_RETRY_DELAY_MS: 3000,         // Пауза перед повтором (мс)

    // Опрос /pages, пока на диске не все PNG. Лимит API — 300 запросов в минуту на IP, за NAT
    // его делят несколько читателей, а 429 на /pages гасит heartbeat — поэтому не чаще.
    // Текущей страницы нет на диске — читатель ждёт её (мс). Сервер сам держит запрос до появления
    // PNG (long-poll, до 1,5 с), поэтому пауза между запросами короткая: ~1 запрос в 1,8 с
    PAGES_POLL_WAITING_MS: 300,
    PAGES_POLL_IDLE_MS: 2000,           // Текущая страница готова; также после сбоя запроса (мс)
    PAGES_POLL_MIN_GAP_MS: 500,         // Между запросами: листание по неготовым страницам (мс)
};

class PngViewer {
    constructor(options = {}) {
        // ИНТЕГРАЦИЯ: Конфигурация режима работы
        this.options = {
            mode: options.mode || 'standalone',        // 'standalone' | 'embedded'
            container: options.container || document,   // корневой элемент
            apiBase: options.apiBase || API.PNG_BASE,  // базовый URL API
            onPageChange: options.onPageChange || null, // callback смены страницы
            initialPage: options.initialPage || 0,      // начальная страница (0-indexed)
        };
        
        this.pages = [];
        this.currentPage = this.options.initialPage;
        this.directory = null;
        this.observer = null;
        this.loadedPages = new Set(); // Отслеживаем загруженные страницы
        this.isScrollingProgrammatically = false; // Флаг для игнорирования программного скролла
        this.goToPageTimeout = null; // Таймер для отмены предыдущих переходов
        this.zoom = CONFIG.DEFAULT_ZOOM; // Текущий масштаб
        this.rotations = new Map(); // Map<pageIndex, degrees>
        this.userInteracted = false; // true после первого явного действия пользователя
        this.diskPageNames = new Set(); // Имена PNG, уже найденных на диске (опрос во время конвертации)
        this.pollTimer = null;          // Единственный таймер опроса /pages (_schedulePoll)
        this.pollInFlight = false;      // Запрос /pages в полёте — второй параллельно не шлём
        this.pollController = null;     // AbortController висящего /pages — прыжок обрывает старый want
        this.lastPagesAt = 0;           // Время последнего запроса /pages (мс)
        this.lastPagesWant = null;      // want последнего запроса /pages — сервер его уже знает

        // ИНТЕГРАЦИЯ: Привязываем DOM элементы (может быть из container)
        this._bindDomElements();

        this.init();
    }

    /**
     * ИНТЕГРАЦИЯ: Привязка DOM элементов с поддержкой custom container
     * В embedded режиме некоторые элементы могут отсутствовать
     */
    _bindDomElements() {
        const root = this.options.container;
        const $ = (sel) => root.querySelector ? root.querySelector(sel) : document.querySelector(sel);
        
        // DOM elements
        this.viewport = $('#viewport');
        this.viewportInner = $('#viewportInner');
        this.pageInput = $('#pageInput');
        this.pageTotal = $('#pageTotal');
        this.zoomDisplay = $('#zoomDisplay');

        // Buttons
        this.firstPageBtn = $('#firstPageBtn');
        this.prevPageBtn = $('#prevPageBtn');
        this.nextPageBtn = $('#nextPageBtn');
        this.lastPageBtn = $('#lastPageBtn');
        this.zoomInBtn = $('#zoomInBtn');
        this.zoomOutBtn = $('#zoomOutBtn');
        this.fitWidthBtn = $('#fitWidthBtn');
        this.fitPageBtn = $('#fitPageBtn');
        this.rotateLeftBtn = $('#rotateLeftBtn');
        this.rotateRightBtn = $('#rotateRightBtn');
    }

    async init() {
        this.setupEventListeners();
        if (!this.initFromHash()) {
            this.showEmptyState('PNG директория не указана');
        }
    }

    /**
     * URL листинга страниц. Запрос — heartbeat просмотра: want (1-based) — страница,
     * которую сейчас показывает вьюер; сервер рендерит её первой.
     * @param {string} dirPath
     * @param {number} want
     * @returns {string}
     */
    _pagesUrl(dirPath, want) {
        // Encode each path segment separately to preserve slashes
        const encodedPath = dirPath.split('/').map(p => encodeURIComponent(p)).join('/');
        return `${this.options.apiBase}/${encodedPath}/pages?want=${want}`;
    }

    /**
     * Собирает список страниц 0..total-1 по имени файла: запись с диска, если PNG уже готов,
     * иначе заглушка с тем же URL (изображение запросится, когда _pollNewPages увидит файл в /pages).
     * Страницы появляются не по порядку (первой рендерится запрошенная), поэтому подставлять
     * ответ сервера по позиции нельзя: при диске [1, 2, 71] третья заглушка получила бы страницу 71.
     * @param {string} dirPath
     * @param {Array<{name: string, url: string, size: number}>} diskPages
     * @param {number} total
     * @returns {Array<{name: string, url: string, size: number}>}
     */
    _buildPageList(dirPath, diskPages, total) {
        const byName = new Map(diskPages.map(p => [p.name, p]));
        const stem = dirPath.split('/').pop().replace(/-png$/, '');
        const baseUrl = diskPages.length > 0
            ? diskPages[0].url.substring(0, diskPages[0].url.lastIndexOf('/') + 1)
            : `/cache/${dirPath}/`;
        const pages = [];
        for (let i = 0; i < total; i++) {
            const name = `${stem}_${String(i + 1).padStart(4, '0')}.png`;
            pages.push(byName.get(name) || { name, url: `${baseUrl}${name}`, size: 0 });
        }
        return pages;
    }

    async loadPages(dirPath, retryCount = 0) {
        const MAX_RETRIES = 15;
        const RETRY_DELAY_MS = 2000;
        try {
            const want = (this.options.initialPage || 0) + 1;
            this.lastPagesAt = Date.now();
            this.lastPagesWant = want;
            const response = await fetch(this._pagesUrl(dirPath, want));

            if (!response.ok) {
                if (retryCount < MAX_RETRIES) {
                    this.showEmptyState('Подготовка страниц...');
                    setTimeout(() => this.loadPages(dirPath, retryCount + 1), RETRY_DELAY_MS);
                    return;
                }
                throw new Error('Failed to load pages');
            }

            const data = await response.json();
            const diskPages = data.pages || [];

            // Обновляем pagesTotal из ответа сервера (pre-scan маркер), если он больше текущего
            if (data.pages_total && data.pages_total > (this.options.pagesTotal || 0)) {
                this.options.pagesTotal = data.pages_total;
            }

            // Директория пуста и нет known total — конвертация ещё не дошла до первой страницы
            if (diskPages.length === 0 && !this.options.pagesTotal && retryCount < MAX_RETRIES) {
                this.showEmptyState('Подготовка страниц...');
                setTimeout(() => this.loadPages(dirPath, retryCount + 1), RETRY_DELAY_MS);
                return;
            }

            this.directory = dirPath;
            // ИНТЕГРАЦИЯ: Сохраняем initialPage, не перезаписываем если он уже установлен
            if (this.options.initialPage === undefined || this.options.initialPage === null) {
                this.currentPage = 0;
            } else {
                this.currentPage = this.options.initialPage;
            }
            this.loadedPages.clear();
            this.rotations.clear(); // Сбросить повороты при загрузке новой директории
            this.setZoom(CONFIG.DEFAULT_ZOOM); // Сбросить масштаб

            // Если известно общее число страниц (конвертация идёт или стоит на паузе),
            // на месте недостающих страниц — заглушки. Вьюер сразу отрисует все контейнеры,
            // а изображения подгрузятся по мере готовности.
            const total = this.options.pagesTotal || diskPages.length;
            this.pages = total > diskPages.length ? this._buildPageList(dirPath, diskPages, total) : diskPages;
            this.diskPageNames = new Set(diskPages.map(p => p.name));

            if (this.pages.length > 0) {
                this.renderAllPages();
                this.updateUI();
                // Пока на диске не все страницы — опрашиваем /pages: подтягиваем новые PNG
                // без перезагрузки вьюера и держим heartbeat, без которого сервер ставит
                // конвертацию на паузу.
                if (this._isPolling()) {
                    this._schedulePoll(this._pollDelay());
                }
            } else {
                this.showEmptyState('Нет PNG файлов в директории');
            }

        } catch (error) {
            console.error('Error loading pages:', error);
            this.showEmptyState('Ошибка загрузки страниц');
        }
    }

    /** Опрос /pages нужен: число страниц известно, и на диске ещё не все. */
    _isPolling() {
        const knownTotal = this.options.pagesTotal;
        return Boolean(knownTotal) && this.diskPageNames.size < knownTotal;
    }

    /**
     * Пауза до следующего опроса: текущей страницы нет на диске — читатель её ждёт, опрос чаще.
     * @returns {number}
     */
    _pollDelay() {
        const page = this.pages[this.currentPage];
        return page && !this.diskPageNames.has(page.name)
            ? CONFIG.PAGES_POLL_WAITING_MS
            : CONFIG.PAGES_POLL_IDLE_MS;
    }

    /**
     * Единственное место, где планируется опрос /pages: прежний таймер сбрасывается, поэтому
     * мгновенный опрос при смене страницы не порождает вторую цепочку опросов.
     * Не раньше PAGES_POLL_MIN_GAP_MS после предыдущего запроса.
     * @param {number} delayMs
     */
    _schedulePoll(delayMs) {
        clearTimeout(this.pollTimer);
        const gapMs = this.lastPagesAt + CONFIG.PAGES_POLL_MIN_GAP_MS - Date.now();
        this.pollTimer = setTimeout(() => {
            this.pollTimer = null;
            this._pollNewPages();
        }, Math.max(delayMs, gapMs, 0));
    }

    /**
     * Смена текущей страницы (скролл или переход). Страницы нет на диске — сервер узнаёт новый want
     * сразу, а не на следующем плановом опросе: иначе ожидание складывалось бы из двух опросов.
     */
    _onCurrentPageChanged() {
        const page = this.pages[this.currentPage];
        if (!this._isPolling() || !page || this.diskPageNames.has(page.name)) return;
        if (this.currentPage + 1 === this.lastPagesWant) return;
        if (this.pollInFlight) {
            // Висящий long-poll держит старый want до 1,5 с — обрываем, новый уйдёт из catch
            this.pollController?.abort();
        } else {
            this._schedulePoll(0);
        }
    }

    /**
     * Лёгкий polling страниц, пока на диске не все PNG.
     * Каждый запрос — heartbeat просмотра: без него сервер через 20 с ставит конвертацию
     * на паузу, а want направляет её к странице, которую сейчас смотрят.
     * Не сбрасывает zoom/rotations/scroll — только подтягивает появившиеся PNG.
     * Страницы появляются не по порядку, поэтому новые определяются по имени файла.
     * Следующий опрос планирует сам через _schedulePoll.
     */
    async _pollNewPages() {
        const knownTotal = this.options.pagesTotal;
        if (!knownTotal) return;

        this.pollInFlight = true;
        this.lastPagesAt = Date.now();
        this.lastPagesWant = this.currentPage + 1;
        this.pollController = new AbortController();
        // Сбой (429, рестарт сервера) — повтор в прежнем темпе, без учащения; null — опрос окончен.
        // AbortError — прыжок на другую страницу: новый want сразу (MIN_GAP ограничивает частоту)
        let nextDelay = CONFIG.PAGES_POLL_IDLE_MS;
        try {
            const response = await fetch(this._pagesUrl(this.directory, this.lastPagesWant), {
                signal: this.pollController.signal,
            });
            if (!response.ok) return;
            const data = await response.json();
            const diskPages = data.pages || [];
            const byName = new Map(diskPages.map(p => [p.name, p]));

            let changed = false;
            const containers = this.viewportInner.querySelectorAll('.page-container');
            this.pages.forEach((page, i) => {
                const diskPage = byName.get(page.name);
                if (!diskPage || this.diskPageNames.has(page.name)) return;
                // Страница появилась на диске — заглушку заменяем реальной записью
                this.diskPageNames.add(page.name);
                this.pages[i] = diskPage;
                changed = true;
                // Грузится или уже показана — не трогаем (повторная загрузка дала бы второй img).
                // Иначе заглушка ждала файла — перепроверяем observer'ом: он загрузит страницу,
                // только если она в зоне viewport + PRELOAD_MARGIN.
                const container = containers[i];
                if (container && this.observer && !this.loadedPages.has(i)) {
                    this.observer.unobserve(container);
                    this.observer.observe(container);
                }
            });
            if (changed) this.updateUI();

            if (diskPages.length >= knownTotal) {
                nextDelay = null;
            } else {
                nextDelay = this._pollDelay();
            }
        } catch (error) {
            if (error?.name === 'AbortError') nextDelay = 0;
            // иначе сбой сети — nextDelay остаётся прежним темпом
        } finally {
            this.pollInFlight = false;
            this.pollController = null;
            if (nextDelay !== null) this._schedulePoll(nextDelay);
        }
    }

    /**
     * ИНТЕГРАЦИЯ: Загрузка PNG директории по пути (без выбора из списка)
     * Используется для embedded режима или прямой навигации
     * @param {string} dirPath - путь вида "archive/archive-png_HASH"
     * @returns {Promise<boolean>} успех загрузки
     */
    async loadDirectory(dirPath) {
        if (!dirPath) {
            this.showEmptyState('Путь к директории не указан');
            return false;
        }
        
        try {
            await this.loadPages(dirPath);
            
            // ИНТЕГРАЦИЯ: Перейти на начальную страницу если указана (>= 0)
            if (typeof this.options.initialPage === 'number' && 
                this.options.initialPage >= 0 && 
                this.options.initialPage < this.pages.length) {
                // ИСПРАВЛЕНИЕ: Используем instant scroll для начальной навигации
                setTimeout(() => {
                    this.goToPage(this.options.initialPage, true);  // instant scroll
                }, 100);
            }
            
            return true;
        } catch (error) {
            console.error('PngViewer.loadDirectory:', error);
            this.showEmptyState('Ошибка загрузки директории');
            return false;
        }
    }

    /**
     * ИНТЕГРАЦИЯ: Инициализация из hash URL
     * Формат: #dir=archive/archive-png&page=5&total=251
     * @returns {boolean} true если параметры найдены и загрузка запущена
     */
    initFromHash() {
        try {
            const hash = window.location.hash.slice(1);
            if (!hash) return false;
            
            const params = new URLSearchParams(hash);
            const dir = params.get('dir');
            const page = parseInt(params.get('page'), 10) || 1;
            const total = parseInt(params.get('total'), 10) || 0;
            
            if (!dir) return false;
            
            // Устанавливаем начальную страницу и общее количество страниц перед загрузкой
            this.options.initialPage = Math.max(0, page - 1); // 0-indexed
            this.options.pagesTotal = total;
            
            this.loadDirectory(dir);
            return true;
        } catch (error) {
            console.warn('initFromHash failed:', error);
            return false;
        }
    }

    setupEventListeners() {
        // Navigation buttons
        this.firstPageBtn.addEventListener('click', () => this.goToPage(0));
        this.prevPageBtn.addEventListener('click', () => this.prevPage());
        this.nextPageBtn.addEventListener('click', () => this.nextPage());
        this.lastPageBtn.addEventListener('click', () => this.goToPage(this.pages.length - 1));

        // Zoom buttons
        this.zoomInBtn.addEventListener('click', () => this.zoomIn());
        this.zoomOutBtn.addEventListener('click', () => this.zoomOut());
        this.fitWidthBtn.addEventListener('click', () => this.fitWidth());
        this.fitPageBtn.addEventListener('click', () => this.fitPage());

        // Rotation buttons
        this.rotateLeftBtn.addEventListener('click', () => this.rotateCurrentPageLeft());
        this.rotateRightBtn.addEventListener('click', () => this.rotateCurrentPageRight());

        // Page input
        this.pageInput.addEventListener('change', (e) => {
            const page = parseInt(e.target.value, 10) - 1;
            if (page >= 0 && page < this.pages.length) {
                this.goToPage(page);
            } else {
                this.pageInput.value = this.currentPage + 1;
            }
        });

        // Keyboard navigation
        document.addEventListener('keydown', (e) => this.onKeyDown(e));

        // Scroll event to update current page
        this.viewport.addEventListener('scroll', () => this.onScroll());

        // Ctrl+Wheel for zoom
        this.viewport.addEventListener('wheel', (e) => {
            if (e.ctrlKey) {
                e.preventDefault();
                if (e.deltaY < 0) {
                    this.zoomIn();
                } else {
                    this.zoomOut();
                }
            }
        }, { passive: false });
        
        // ИНТЕГРАЦИЯ: postMessage listener для команд от родительского окна
        // Позволяет single.js управлять viewer'ом через postMessage
        window.addEventListener('message', (event) => {
            // SECURITY: принимаем только от same-origin
            if (event.origin !== window.location.origin) return;
            
            // Обрабатываем команду перехода на страницу
            if (event.data?.type === POSTMESSAGE_TYPES.GOTO_PAGE) {
                const pageNumber = Number(event.data?.pageNumber);
                if (!Number.isFinite(pageNumber) || pageNumber < 1) return;
                
                // Переходим на страницу (1-indexed -> 0-indexed)
                this.goToPage(pageNumber - 1);
            }
        });

        // Помечаем первое явное действие пользователя, чтобы разрешить пересчёт
        // текущей страницы по скроллу (см. onScroll). До взаимодействия scroll-эхо
        // от scrollIntoView и reflow от дозагрузки PNG не должны менять ни счётчик
        // страниц в тулбаре, ни p в hash родителя.
        const markInteracted = () => { this.userInteracted = true; };
        this.viewport.addEventListener('wheel',       markInteracted, { once: true, passive: true });
        this.viewport.addEventListener('pointerdown', markInteracted, { once: true });
        document.addEventListener     ('keydown',     markInteracted, { once: true });
    }

    onKeyDown(e) {
        if (!this.pages.length) return;

        // Navigation
        if (e.key === 'ArrowLeft' || e.key === 'PageUp') {
            e.preventDefault();
            this.prevPage();
        } else if (e.key === 'ArrowRight' || e.key === 'PageDown') {
            e.preventDefault();
            this.nextPage();
        } else if (e.key === 'Home') {
            e.preventDefault();
            this.goToPage(0);
        } else if (e.key === 'End') {
            e.preventDefault();
            this.goToPage(this.pages.length - 1);
        }
        // Zoom
        else if (e.key === '+' || e.key === '=') {
            e.preventDefault();
            this.zoomIn();
        } else if (e.key === '-') {
            e.preventDefault();
            this.zoomOut();
        } else if (e.key === 'w' || e.key === 'W') {
            e.preventDefault();
            this.fitWidth();
        } else if (e.key === 'p' || e.key === 'P') {
            e.preventDefault();
            this.fitPage();
        }
        // Rotation
        else if (e.key === 'r' || e.key === 'R') {
            e.preventDefault();
            if (e.shiftKey) {
                this.rotateCurrentPageLeft();
            } else {
                this.rotateCurrentPageRight();
            }
        }
    }

    onScroll() {
        if (!this.pages.length) return;
        if (this.isScrollingProgrammatically) return; // Игнорируем программный скролл
        // До первого явного действия пользователя scroll-эхо от scrollIntoView
        // и reflow от дозагрузки PNG не должны менять ни счётчик страниц, ни URL.
        if (!this.userInteracted) return;

        // Throttle scroll updates
        if (this.scrollTimeout) return;
        this.scrollTimeout = setTimeout(() => {
            this.scrollTimeout = null;
            this.updateCurrentPageFromScroll();
        }, CONFIG.SCROLL_THROTTLE_MS);
    }

    updateCurrentPageFromScroll() {
        const containers = this.viewportInner.querySelectorAll('.page-container');
        if (!containers.length) return;

        const viewportRect = this.viewport.getBoundingClientRect();
        const viewportCenterY = viewportRect.top + viewportRect.height / 2;

        let newCurrentPage = 0;
        let minDistance = Infinity;
        
        containers.forEach((container, index) => {
            const rect = container.getBoundingClientRect();
            const containerCenterY = rect.top + rect.height / 2;
            const distance = Math.abs(containerCenterY - viewportCenterY);
            
            if (distance < minDistance) {
                minDistance = distance;
                newCurrentPage = index;
            }
        });

        if (this.currentPage !== newCurrentPage) {
            this.currentPage = newCurrentPage;
            this.updatePageIndicator();
            this._notifyPageChange();  // ИНТЕГРАЦИЯ: Уведомление о смене страницы
            this._onCurrentPageChanged();
        }
    }

    renderAllPages() {
        this.viewportInner.innerHTML = '';

        this.pages.forEach((page, index) => {
            const container = document.createElement('div');
            container.className = 'page-container';
            container.dataset.pageIndex = index;

            // Create placeholder
            const placeholder = document.createElement('div');
            placeholder.className = 'page-placeholder';
            placeholder.textContent = `Загрузка страницы ${index + 1}...`;
            container.appendChild(placeholder);

            this.viewportInner.appendChild(container);
        });

        this.setupIntersectionObserver();
    }

    setupIntersectionObserver() {
        // Clean up previous observer
        if (this.observer) {
            this.observer.disconnect();
        }

        const options = {
            root: this.viewport,
            rootMargin: CONFIG.PRELOAD_MARGIN,
            threshold: CONFIG.INTERSECTION_THRESHOLD
        };

        this.observer = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const pageIndex = parseInt(entry.target.dataset.pageIndex, 10);
                    if (!this.loadedPages.has(pageIndex)) {
                        this.loadPageImage(entry.target, pageIndex);
                    }
                }
            });
        }, options);

        // Observe all page containers
        this.viewportInner.querySelectorAll('.page-container').forEach(container => {
            this.observer.observe(container);
        });
    }

    loadPageImage(container, pageIndex) {
        const page = this.pages[pageIndex];
        if (!page) return;

        // Заглушка уже на экране: спиннер (is-busy) показывает, что ожидание живое, а не зависание.
        // Текст меняется через textContent, поэтому спиннер — ::before, а не дочерний элемент
        const placeholder = container.querySelector('.page-placeholder');

        // PNG ещё нет в /pages — не запрашиваем: 404 по каждой видимой заглушке съедал бы
        // лимит запросов, и heartbeat /pages получал бы 429. Когда файл появится,
        // _pollNewPages перепроверит контейнер observer'ом.
        if (!this.diskPageNames.has(page.name)) {
            if (placeholder) {
                placeholder.textContent = `Страница ${pageIndex + 1} подготавливается...`;
                placeholder.classList.add('is-busy');
            }
            return;
        }

        // Mark as loading to prevent duplicate requests from IntersectionObserver
        this.loadedPages.add(pageIndex);

        // Файл есть в /pages — этап сменился: рендер закончился, PNG скачивается
        if (placeholder) {
            placeholder.textContent = `Страница ${pageIndex + 1} загружается...`;
            placeholder.classList.add('is-busy');
        }

        const img = new Image();
        img.className = 'page-image';
        img.alt = `Страница ${pageIndex + 1}`;

        img.onload = () => {
            // Remove placeholder
            if (placeholder) {
                placeholder.remove();
            }
            
            // Применить сохраненный поворот, если есть
            const rotation = this.rotations.get(pageIndex);
            if (rotation) {
                img.style.transform = `rotate(${rotation}deg)`;
            }
            
            container._retryCount = 0;
            // Add loaded image
            container.appendChild(img);
        };

        img.onerror = () => {
            // Файл есть на диске, значит сбой временный (429, сеть). Повторяем ограниченно
            // и только для страницы на экране, чтобы пролистанные страницы не ретраили фоном.
            // Снимаем метку "загружается" — уход и возврат к странице дадут новую попытку
            this.loadedPages.delete(pageIndex);
            const retryCount = (container._retryCount || 0) + 1;
            container._retryCount = retryCount;
            if (retryCount > CONFIG.IMAGE_RETRY_MAX) {
                if (placeholder) {
                    placeholder.textContent = `Страница ${pageIndex + 1}: не удалось загрузить`;
                    // Спиннер на окончательном отказе читался бы как «ещё грузится»
                    placeholder.classList.remove('is-busy');
                }
                // Счётчик обнуляем: когда страница снова попадёт в зону viewport, observer
                // запустит полный цикл попыток, а не одну без повтора
                container._retryCount = 0;
                return;
            }
            if (placeholder) {
                placeholder.textContent = `Страница ${pageIndex + 1}: не удалось загрузить, повтор...`;
            }
            setTimeout(() => {
                // observer загрузит страницу, только если она в зоне viewport + PRELOAD_MARGIN
                if (this.observer && container.isConnected && !this.loadedPages.has(pageIndex)) {
                    this.observer.unobserve(container);
                    this.observer.observe(container);
                }
            }, CONFIG.IMAGE_RETRY_DELAY_MS);
        };

        img.src = page.url;
    }

    /**
     * ИНТЕГРАЦИЯ: Уведомление родителя о смене страницы
     * Совместимо с форматом postMessage для минимальных изменений в single.js
     */
    _notifyPageChange() {
        if (this.options.mode !== 'embedded') return;
        
        const message = {
            type: POSTMESSAGE_TYPES.PAGE_CHANGE,
            pageNumber: this.currentPage + 1,  // 1-indexed для совместимости
            totalPages: this.pages.length,
            directory: this.directory
        };
        
        // Отправить в parent window (iframe режим)
        if (window.parent !== window) {
            window.parent.postMessage(message, '*');
        }
        
        // Callback если задан
        if (typeof this.options.onPageChange === 'function') {
            this.options.onPageChange(message);
        }
    }

    goToPage(pageIndex, instant = false) {
        if (pageIndex < 0 || pageIndex >= this.pages.length) return;

        const container = this.viewportInner.querySelector(
            `.page-container[data-page-index="${pageIndex}"]`
        );
        
        if (container) {
            // Отменить предыдущий таймер
            if (this.goToPageTimeout) {
                clearTimeout(this.goToPageTimeout);
            }
            
            this.isScrollingProgrammatically = true;
            this.currentPage = pageIndex;
            this.updatePageIndicator();
            this._notifyPageChange();  // ИНТЕГРАЦИЯ: Уведомление о смене страницы
            this._onCurrentPageChanged();

            // ИСПРАВЛЕНИЕ: instant scroll для начальной навигации
            container.scrollIntoView({ 
                behavior: instant ? 'instant' : 'smooth', 
                block: 'start' 
            });
            
            if (instant) {
                // Мгновенный скролл - сразу сбрасываем флаг
                this.isScrollingProgrammatically = false;
            } else {
                // Плавный скролл - ждём завершения анимации
                this.goToPageTimeout = setTimeout(() => {
                    this.isScrollingProgrammatically = false;
                    this.currentPage = pageIndex;
                    this.updatePageIndicator();
                    this._notifyPageChange();  // ИНТЕГРАЦИЯ: Уведомление после завершения скролла
                }, CONFIG.SMOOTH_SCROLL_DELAY_MS);
            }
        }
    }

    nextPage() {
        if (this.currentPage < this.pages.length - 1) {
            this.goToPage(this.currentPage + 1);
        }
    }

    prevPage() {
        if (this.currentPage > 0) {
            this.goToPage(this.currentPage - 1);
        }
    }

    setZoom(newZoom) {
        // Ограничить значение
        this.zoom = Math.max(CONFIG.MIN_ZOOM, Math.min(CONFIG.MAX_ZOOM, newZoom));
        
        // Применить CSS transform
        this.viewportInner.style.transform = `scale(${this.zoom})`;
        this.viewportInner.style.transformOrigin = 'center top';
        
        // Обновить отображение
        this.zoomDisplay.textContent = `${Math.round(this.zoom * 100)}%`;
    }

    zoomIn() {
        this.setZoom(this.zoom + CONFIG.ZOOM_STEP);
    }

    zoomOut() {
        this.setZoom(this.zoom - CONFIG.ZOOM_STEP);
    }

    fitWidth() {
        if (!this.pages.length) return;
        
        // Найти первую загруженную страницу для вычисления размера
        const firstImage = this.viewportInner.querySelector('.page-image');
        if (!firstImage) return;
        
        const viewportWidth = this.viewport.clientWidth;
        const pageWidth = firstImage.naturalWidth;
        
        if (pageWidth > 0) {
            const zoom = (viewportWidth - 40) / pageWidth; // 40px для padding
            this.setZoom(zoom);
        }
    }

    fitPage() {
        if (!this.pages.length) return;
        
        // Найти первую загруженную страницу для вычисления размера
        const firstImage = this.viewportInner.querySelector('.page-image');
        if (!firstImage) return;
        
        const viewportWidth = this.viewport.clientWidth;
        const viewportHeight = this.viewport.clientHeight;
        const pageWidth = firstImage.naturalWidth;
        const pageHeight = firstImage.naturalHeight;
        
        if (pageWidth > 0 && pageHeight > 0) {
            const zoomW = (viewportWidth - 40) / pageWidth;
            const zoomH = (viewportHeight - 40) / pageHeight;
            const zoom = Math.min(zoomW, zoomH);
            this.setZoom(zoom);
        }
    }

    rotatePage(pageIndex, delta) {
        if (pageIndex < 0 || pageIndex >= this.pages.length) return;
        
        // Получить текущий угол
        const currentRotation = this.rotations.get(pageIndex) || 0;
        const newRotation = (currentRotation + delta + 360) % 360;
        
        // Сохранить новый угол
        this.rotations.set(pageIndex, newRotation);
        
        // Применить к изображению, если оно загружено
        const container = this.viewportInner.querySelector(
            `.page-container[data-page-index="${pageIndex}"]`
        );
        if (container) {
            const img = container.querySelector('.page-image');
            if (img) {
                img.style.transform = `rotate(${newRotation}deg)`;
            }
        }
    }

    rotateCurrentPageLeft() {
        this.rotatePage(this.currentPage, -CONFIG.ROTATION_STEP);
    }

    rotateCurrentPageRight() {
        this.rotatePage(this.currentPage, CONFIG.ROTATION_STEP);
    }

    updateUI() {
        const hasPages = this.pages.length > 0;
        
        // Update page total
        this.pageTotal.textContent = `/ ${this.pages.length}`;

        // Update navigation buttons
        this.firstPageBtn.disabled = !hasPages;
        this.prevPageBtn.disabled = !hasPages;
        this.nextPageBtn.disabled = !hasPages;
        this.lastPageBtn.disabled = !hasPages;

        // Page input
        this.pageInput.disabled = !hasPages;
        this.pageInput.max = this.pages.length;

        // Zoom buttons
        this.zoomInBtn.disabled = !hasPages;
        this.zoomOutBtn.disabled = !hasPages;
        this.fitWidthBtn.disabled = !hasPages;
        this.fitPageBtn.disabled = !hasPages;
        this.zoomDisplay.textContent = `${Math.round(this.zoom * 100)}%`;

        // Rotation buttons
        this.rotateLeftBtn.disabled = !hasPages;
        this.rotateRightBtn.disabled = !hasPages;

        // Update page indicator
        this.updatePageIndicator();
    }

    updatePageIndicator() {
        this.pageInput.value = this.pages.length > 0 ? this.currentPage + 1 : 0;
    }

    showEmptyState(message) {
        this.pages = [];
        this.currentPage = 0;
        this.loadedPages.clear();
        
        if (this.observer) {
            this.observer.disconnect();
            this.observer = null;
        }

        this.viewportInner.innerHTML = `
            <div class="empty-state">
                <h2>PNG Viewer MVP</h2>
                <p>${message}</p>
            </div>
        `;
        
        this.updateUI();
    }

}

// ==========================================================================
// ESM ЭКСПОРТЫ
// ==========================================================================
export { PngViewer };
