// Version 1.3 - 15.06.2026
// Кнопки авторизации и публикации в футере левой панели главной страницы.
// Проверяет /api/auth/me, переключает UI и обрабатывает вход (редирект) / выход.
// Изменения v1.1: повторная проверка авторизации при возврате на вкладку (visibilitychange/focus).
// Изменения v1.2: переведён на authService (getCurrentUser/logout).
// Изменения v1.3: исправлен висячий вызов удалённого checkAuth() в recheckAuthOnReturn -> getCurrentUser().

import { getCurrentUser, logout } from '../services/authService.js';

const UPLOAD_URL = 'upload.html';

function renderSidebarAuth(user) {
    const publishBtn = document.getElementById('publishReportBtn');
    const authBtn = document.getElementById('sidebarAuthBtn');
    if (!publishBtn || !authBtn) return;

    authBtn.classList.toggle('is-authenticated', Boolean(user));

    if (user) {
        publishBtn.href = UPLOAD_URL;
        publishBtn.setAttribute('aria-disabled', 'false');
        authBtn.title = 'Выйти';
        authBtn.setAttribute('aria-label', 'Выйти');
    } else {
        publishBtn.removeAttribute('href');
        publishBtn.setAttribute('aria-disabled', 'true');
        authBtn.title = 'Войти';
        authBtn.setAttribute('aria-label', 'Войти');
    }
}

export async function initSidebarAuth() {
    const authBtn = document.getElementById('sidebarAuthBtn');
    if (!authBtn) return;

    let user = await getCurrentUser();
    renderSidebarAuth(user);

    async function recheckAuthOnReturn() {
        if (user) return;
        const fresh = await getCurrentUser();
        if (!fresh) return;
        user = fresh;
        renderSidebarAuth(user);
    }
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') recheckAuthOnReturn();
    });
    window.addEventListener('focus', recheckAuthOnReturn);

    authBtn.addEventListener('click', async () => {
        if (user) {
            await logout();
            user = null;
            renderSidebarAuth(null);
        } else {
            window.open(UPLOAD_URL, '_blank', 'noopener');
        }
    });
}
