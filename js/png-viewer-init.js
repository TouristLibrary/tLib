// Version 1.0 - 29.01.2026
// PNG Viewer Initialization для embedded режима
// СОВМЕСТИМОСТЬ: Внешний файл для обхода CSP ограничений на inline scripts

// ВАЖНО: При изменении импортов обновите
// список <link rel="modulepreload"> в png-viewer.html

import { PngViewer } from './png-viewer.js';

// Инициализация viewer в embedded режиме (источник: hash #dir=...&page=...)
const viewer = new PngViewer({ mode: 'embedded' });

// Для отладки и доступа из консоли
window.pngViewer = viewer;
