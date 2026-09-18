// Version 1.0 - 18.09.2026 07:50:00 GMT
// Объявление в футере левой панели главной страницы.
// Текст приходит в /api/config (data.notice); пустой text — слот скрыт.
// Если href задан сервером, вся фраза — одна ссылка. Без innerHTML.

import { getServerConfig } from '../services/serverConfigService.js';

export function initSiteNotice() {
    const el = document.getElementById('siteNotice');
    if (!el) return;

    try {
        const notice = getServerConfig().notice || {};
        const text = typeof notice.text === 'string' ? notice.text : '';
        const href = typeof notice.href === 'string' ? notice.href : '';

        el.replaceChildren();
        if (!text) {
            el.hidden = true;
            return;
        }

        if (href) {
            const link = document.createElement('a');
            link.href = href;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.textContent = text;
            el.appendChild(link);
        } else {
            el.textContent = text;
        }
        el.hidden = false;
    } catch (error) {
        el.hidden = true;
        console.error('Не удалось показать объявление:', error);
    }
}
