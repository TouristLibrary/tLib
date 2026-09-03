// Version 3.3 - 27.05.2026 - userInteracted gate перенесён в onScroll
// PNG Viewer - ESM модуль для просмотра PNG страниц
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
// - GET {apiBase}/{path}/pages - список страниц в директории
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

    async loadPages(dirPath, retryCount = 0) {
        const MAX_RETRIES = 15;
        const RETRY_DELAY_MS = 2000;
        try {
            // Encode each path segment separately to preserve slashes
            const parts = dirPath.split('/');
            const encodedPath = parts.map(p => encodeURIComponent(p)).join('/');
            const response = await fetch(`${this.options.apiBase}/${encodedPath}/pages`);

            if (!response.ok) {
                if (retryCount < MAX_RETRIES) {
                    this.showEmptyState('Подготовка страниц...');
                    setTimeout(() => this.loadPages(dirPath, retryCount + 1), RETRY_DELAY_MS);
                    return;
                }
                throw new Error('Failed to load pages');
            }
            
            const data = await response.json();
            this.pages = data.pages || [];

            // Обновляем pagesTotal из ответа сервера (pre-scan маркер), если он больше текущего
            if (data.pages_total && data.pages_total > (this.options.pagesTotal || 0)) {
                this.options.pagesTotal = data.pages_total;
            }

            // Директория пуста и нет known total — конвертация ещё не дошла до первой страницы
            if (this.pages.length === 0 && !this.options.pagesTotal && retryCount < MAX_RETRIES) {
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
            // Сбросить счётчики retry для всех контейнеров
            this.viewportInner.querySelectorAll('.page-container').forEach(c => { c._retryCount = 0; });

            // Если нам известно общее число страниц (конвертация ещё идёт),
            // добавляем записи-заглушки для страниц, которых ещё нет на диске.
            // Вьюер сразу отрисует все контейнеры, а изображения подгрузятся по мере готовности.
            const total = this.options.pagesTotal || this.pages.length;
            if (total > this.pages.length) {
                const dirName = dirPath.split('/').pop();
                const stem = dirName.replace(/-png$/, '');
                const baseUrl = this.pages.length > 0
                    ? this.pages[0].url.substring(0, this.pages[0].url.lastIndexOf('/') + 1)
                    : `/cache/${dirPath}/`;
                for (let i = this.pages.length; i < total; i++) {
                    const num = String(i + 1).padStart(4, '0');
                    this.pages.push({ name: `${stem}_${num}.png`, url: `${baseUrl}${stem}_${num}.png`, size: 0 });
                }
            }

            if (this.pages.length > 0) {
                this.renderAllPages();
                this.updateUI();
                // Если конвертация ещё идёт (известно total, но на диске меньше страниц),
                // запускаем лёгкий polling для подтягивания новых PNG без перезагрузки вьюера.
                const knownTotal = this.options.pagesTotal;
                const diskPages = data.pages.length;
                if (knownTotal && diskPages < knownTotal) {
                    setTimeout(() => this._pollNewPages(dirPath, diskPages), RETRY_DELAY_MS);
                }
            } else {
                this.showEmptyState('Нет PNG файлов в директории');
            }

        } catch (error) {
            console.error('Error loading pages:', error);
            this.showEmptyState('Ошибка загрузки страниц');
        }
    }

    /**
     * Лёгкий polling новых страниц во время конвертации.
     * Не сбрасывает zoom/rotations/scroll — только добавляет новые контейнеры.
     * @param {string} dirPath
     * @param {number} knownDiskCount - количество страниц на диске при предыдущем опросе
     */
    async _pollNewPages(dirPath, knownDiskCount) {
        const RETRY_DELAY_MS = 2000;
        const knownTotal = this.options.pagesTotal;
        if (!knownTotal) return;

        try {
            const parts = dirPath.split('/');
            const encodedPath = parts.map(p => encodeURIComponent(p)).join('/');
            const response = await fetch(`${this.options.apiBase}/${encodedPath}/pages`);
            if (!response.ok) {
                if (knownDiskCount < knownTotal) {
                    setTimeout(() => this._pollNewPages(dirPath, knownDiskCount), RETRY_DELAY_MS);
                }
                return;
            }
            const data = await response.json();
            const newDiskCount = (data.pages || []).length;

            if (newDiskCount > knownDiskCount) {
                // Новые страницы появились — обновляем существующие контейнеры
                // (img src для страниц, которые уже есть на диске, будут загружены IntersectionObserver'ом)
                // Обновляем заглушки: страницы с index < newDiskCount теперь реальные
                const containers = this.viewportInner.querySelectorAll('.page-container');
                for (let i = knownDiskCount; i < newDiskCount && i < containers.length; i++) {
                    const page = data.pages[i];
                    if (!page) continue;
                    const container = containers[i];
                    if (!container) continue;
                    // Обновляем запись в this.pages
                    this.pages[i] = page;
                    // Если изображение ещё не загружено — убираем флаг loaded чтобы observer подгрузил
                    this.loadedPages.delete(i);
                    // Сигнализируем observer'у перепроверить контейнер
                    if (this.observer) {
                        this.observer.unobserve(container);
                        this.observer.observe(container);
                    }
                }
                this.updateUI();
            }

            if (newDiskCount < knownTotal) {
                setTimeout(() => this._pollNewPages(dirPath, newDiskCount), RETRY_DELAY_MS);
            }
        } catch {
            if (knownDiskCount < knownTotal) {
                setTimeout(() => this._pollNewPages(dirPath, knownDiskCount), RETRY_DELAY_MS);
            }
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

        // Mark as loading to prevent duplicate requests from IntersectionObserver
        this.loadedPages.add(pageIndex);

        const img = new Image();
        img.className = 'page-image';
        img.alt = `Страница ${pageIndex + 1}`;

        img.onload = () => {
            // Remove placeholder
            const placeholder = container.querySelector('.page-placeholder');
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
            // Страница ещё не готова — показываем сообщение и повторяем попытку с backoff
            const placeholder = container.querySelector('.page-placeholder');
            if (placeholder) {
                placeholder.textContent = `Страница ${pageIndex + 1} подготавливается...`;
                placeholder.style.color = '';
            }
            // Снимаем метку "загружается" — позволяет retry сработать
            this.loadedPages.delete(pageIndex);
            const retryCount = (container._retryCount || 0) + 1;
            container._retryCount = retryCount;
            const retryDelay = Math.min(3000 * Math.pow(1.5, retryCount - 1), 15000);
            setTimeout(() => {
                if (!this.loadedPages.has(pageIndex)) {
                    this.loadPageImage(container, pageIndex);
                }
            }, retryDelay);
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
