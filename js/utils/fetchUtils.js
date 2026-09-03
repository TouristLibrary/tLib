// Version 2.0 - 26.02.2026
// Описание: Утилиты для HTTP-запросов с поддержкой retry и таймаута.
//           fetchJson: базовая проверка HTTP-статуса + парсинг JSON, retry с exponential backoff, timeout через AbortController.
//           fetchApiJson: дополнительная проверка поля success в ответе API.
//           fetchApiJsonSafe: то же, что fetchApiJson, но возвращает {ok, json} вместо throw.
//           Все функции принимают опциональный третий аргумент retryConfig для переопределения defaults.
//           Retry выполняется при: сетевой ошибке, таймауте, HTTP 5xx, HTTP 429.
//           Retry НЕ выполняется при: HTTP 4xx (кроме 429) — клиентская ошибка, повторять бессмысленно.

/**
 * Параметры retry по умолчанию
 */
const RETRY_DEFAULTS = {
    retries: 3,          // количество повторных попыток (0 = без retry)
    timeoutMs: 15000,    // таймаут одного запроса в мс
    backoffMs: 1000,     // начальная пауза между попытками в мс
    backoffFactor: 2     // множитель для exponential backoff
};

/**
 * Выполняет fetch-запрос с таймаутом через AbortController.
 * @param {string} url
 * @param {RequestInit} options
 * @param {number} timeoutMs
 * @returns {Promise<Response>}
 */
async function _fetchWithTimeout(url, options, timeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    try {
        return await fetch(url, { ...options, signal: controller.signal });
    } catch (error) {
        if (error.name === 'AbortError') {
            throw new Error(`Превышено время ожидания (${timeoutMs}мс): ${url}`);
        }
        throw error;
    } finally {
        clearTimeout(timer);
    }
}

/**
 * Определяет, следует ли повторить запрос при данной ошибке/статусе.
 * @param {Error|null} error - ошибка сети/таймаута (null если была ошибка HTTP)
 * @param {number|null} status - HTTP статус (null если была сетевая ошибка)
 * @returns {boolean}
 */
function _shouldRetry(error, status) {
    if (error) return true;          // сетевая ошибка или таймаут — всегда retry
    if (status === 429) return true; // Too Many Requests
    if (status >= 500) return true;  // серверная ошибка
    return false;                    // 4xx (кроме 429) — не retry
}

/**
 * Выполняет fetch-запрос и возвращает JSON.
 * Бросает Error при !response.ok или ошибке сети.
 * @param {string} url
 * @param {RequestInit} [options]
 * @param {Partial<typeof RETRY_DEFAULTS>} [retryConfig]
 * @returns {Promise<any>}
 */
export async function fetchJson(url, options, retryConfig) {
    const cfg = { ...RETRY_DEFAULTS, ...retryConfig };
    let lastError;
    let delay = cfg.backoffMs;

    for (let attempt = 0; attempt <= cfg.retries; attempt++) {
        if (attempt > 0) {
            await new Promise(resolve => setTimeout(resolve, delay));
            delay = Math.min(delay * cfg.backoffFactor, 30000);
            console.warn(`fetchJson: retry ${attempt}/${cfg.retries} для ${url}`);
        }

        try {
            const response = await _fetchWithTimeout(url, options ?? {}, cfg.timeoutMs);
            if (!response.ok) {
                if (!_shouldRetry(null, response.status)) {
                    throw new Error(`HTTP ${response.status}`);
                }
                lastError = new Error(`HTTP ${response.status}`);
                continue;
            }
            return await response.json();
        } catch (error) {
            lastError = error;
            if (!_shouldRetry(error, null)) {
                throw error;
            }
        }
    }

    throw lastError;
}

/**
 * Выполняет fetch-запрос к API-эндпоинту и возвращает JSON.
 * Бросает Error при !response.ok, ошибке сети или !json.success.
 * @param {string} url
 * @param {RequestInit} [options]
 * @param {Partial<typeof RETRY_DEFAULTS>} [retryConfig]
 * @returns {Promise<any>}
 */
export async function fetchApiJson(url, options, retryConfig) {
    const json = await fetchJson(url, options, retryConfig);
    if (!json.success) {
        throw new Error(json.error || 'Ошибка сервера');
    }
    return json;
}

/**
 * Выполняет fetch-запрос к API-эндпоинту и возвращает Result-объект вместо throw.
 * Удобен там, где нужна retry-логика без try/catch на каждый вызов.
 * @param {string} url
 * @param {RequestInit} [options]
 * @param {Partial<typeof RETRY_DEFAULTS>} [retryConfig]
 * @returns {Promise<{ok: true, json: any}|{ok: false, error: string}>}
 */
export async function fetchApiJsonSafe(url, options, retryConfig) {
    try {
        return { ok: true, json: await fetchApiJson(url, options, retryConfig) };
    } catch (error) {
        return { ok: false, error: error?.message || String(error) };
    }
}
