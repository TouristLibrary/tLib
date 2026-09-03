// Version 1.1 - 21.06.2026
// Единый клиент авторизации (magic link + код): запросы к /api/auth/*.
// Без DOM: возвращает структурированный результат, UI-логика остаётся на страницах.
// Изменения v1.1: logout({ all }) — выход со всех устройств через ?all=1.

import { API } from '../config/api.config.js';

/**
 * Проверяет текущую сессию.
 * @returns {Promise<Object|null>} Объект пользователя или null, если не авторизован.
 */
export async function getCurrentUser() {
    try {
        const res = await fetch(API.AUTH_ME);
        if (!res.ok) return null;
        return await res.json();
    } catch { return null; }
}

/**
 * Запрашивает magic link / код на email.
 * @param {{ email: string, name?: string, redirect?: string }} options
 * @returns {Promise<{ ok: boolean, status: number, data: Object }>}
 */
export async function requestLink({ email, name, redirect } = {}) {
    const body = { email };
    if (name) body.name = name;
    if (redirect) body.redirect = redirect;
    const res = await fetch(API.AUTH_REQUEST_LINK, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data };
}

/**
 * Верифицирует цифровой код из письма.
 * @param {string} email
 * @param {string} code
 * @returns {Promise<{ ok: boolean, status: number, data: Object }>}
 */
export async function verifyCode(email, code) {
    const res = await fetch(API.AUTH_VERIFY_CODE, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, code }),
    });
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data };
}

/**
 * Завершает сессию (logout). Ошибки сети игнорируются.
 * @param {{ all?: boolean }} [options] - all: true — выход со всех устройств (?all=1).
 * @returns {Promise<void>}
 */
export async function logout({ all = false } = {}) {
    try {
        const url = all ? `${API.AUTH_LOGOUT}?all=1` : API.AUTH_LOGOUT;
        await fetch(url, { method: 'POST' });
    } catch { /* игнорируем ошибку сети */ }
}
