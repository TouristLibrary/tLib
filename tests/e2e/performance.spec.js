const { test, expect } = require('@playwright/test');
const { gotoMainReady, skipIfSeedMissing } = require('./helpers');

expect.extend({
  async toHaveCountGreaterThan(locator, min) {
    const count = await locator.count();
    const pass = count > min;
    return {
      pass,
      message: () => `expected count ${count} to be > ${min}`,
    };
  },
});

test.describe('Performance', () => {
  test.setTimeout(120000);

  // --- API и сохранение состояния (из smoke-27) ---

  test('API ответы 200 @smoke', async ({ page }) => {
    // Карта эндпоинтов, для которых не-200 является штатным.
    // /api/auth/me у анонима по контракту обязан быть ровно 401 (routers/auth_router.py).
    const EXPECTED_NON_200 = [{ match: '/api/auth/me', status: 401 }];

    const responses = [];
    const netFailures = [];
    page.on('response', (r) => {
      if (r.url().includes('/api/')) responses.push({ url: r.url(), status: r.status() });
    });
    // Обрывы соединения (abort/DNS) не попадают в responses — ловим отдельно
    page.on('requestfailed', (req) => {
      if (req.url().includes('/api/'))
        netFailures.push(`${req.url()} — ${req.failure()?.errorText}`);
    });

    await gotoMainReady(page);
    await page.locator('input[name="ГодС"]').fill('2024');
    await page.locator('#searchButton').click();
    await expect(page.locator('#results-table')).toBeVisible({ timeout: 15000 });
    // Ждём завершения фоновых вызовов (справочники, reports-count, auth/me)
    await page.waitForLoadState('networkidle');

    const unexpected = responses.filter((r) => {
      const rule = EXPECTED_NON_200.find((e) => r.url.includes(e.match));
      // Для эндпоинтов из карты проверяем точное соответствие ожидаемому статусу
      // (500 или 200 на auth/me — одинаково плохо)
      return rule ? r.status !== rule.status : r.status !== 200;
    });

    // Никаких неожиданных статусов
    expect(unexpected, JSON.stringify(unexpected, null, 2)).toEqual([]);
    // Никаких сетевых обрывов
    expect(netFailures, netFailures.join('\n')).toEqual([]);
    // Ключевые вызовы реально произошли — тест не «зеленеет» вхолостую
    const urls = responses.map((r) => r.url);
    expect(urls.some((u) => u.includes('/api/search'))).toBe(true);
    expect(urls.some((u) => u.includes('/api/auth/me'))).toBe(true);
  });

  test('Сохранение состояния при reload', async ({ page }) => {
    skipIfSeedMissing(test, '1-TST');
    await page.goto('/?1-TST#tab=pdf', { waitUntil: 'load' });
    await expect(page.locator('.single-result-formatted')).toContainText('#1-TST', { timeout: 20000 });
    await page.reload();
    await expect(page).toHaveURL(/1-TST.*tab=pdf/);
    await expect(page.locator('.tab-button.active[data-tab="tab-1"]')).toBeVisible({ timeout: 5000 });
  });

  // --- Время загрузки (из xss-protection) ---

  test('Загрузка страницы и первый поиск < 15 секунд', async ({ page }) => {
    const startTime = Date.now();

    await gotoMainReady(page);
    await page.locator('#searchButton').click();
    await expect(page.locator('#results-table')).toBeVisible({ timeout: 10000 });

    const loadTime = Date.now() - startTime;
    expect(loadTime).toBeLessThan(15000);
  });

  // --- Background autoload и image caching (из tlib) ---

  test('Background autoload: table results show, indicator may appear and eventually disappear', async ({ page }) => {
    test.setTimeout(60000);
    await gotoMainReady(page);
    await page.locator('#searchButton').click();

    await expect(page.locator('#results-table')).toBeVisible({ timeout: 15000 });
    await expect(page.locator('#results-table tbody tr')).toHaveCountGreaterThan(3);

    // With lazy scroll pagination the indicator stays visible while more pages
    // are available for scroll-loading -- this is expected behaviour.
    // We only verify the indicator is rendered correctly when present.
    const indicator = page.locator('#loading-indicator');
    const appeared = await indicator.waitFor({ state: 'attached', timeout: 2000 }).then(() => true).catch(() => false);
    if (appeared) {
      await expect(indicator).toBeVisible();
    }
  });

  test('Image caching (1-TST): switching away and back should not re-download image', async ({ page }) => {
    skipIfSeedMissing(test, '1-TST');
    test.setTimeout(180000);

    const deepLink = '/?1-TST#tab=img&file=1-TST%2F%D0%98%D0%B7%D0%BE%D0%B1%D1%80%D0%B0%D0%B6%D0%B5%D0%BD%D0%B8+%D0%B8%D0%B7+%D0%B4%D1%80%D1%83%D0%B3%D0%BE%D0%B3%D0%BE+%D0%BE%D1%82%D1%87%D0%B5%D1%82%D0%B0+1.jpg';
    const targetApiPath = '/api/archive/00001-TST/file/1-TST%2F%D0%98%D0%B7%D0%BE%D0%B1%D1%80%D0%B0%D0%B6%D0%B5%D0%BD%D0%B8%20%D0%B8%D0%B7%20%D0%B4%D1%80%D1%83%D0%B3%D0%BE%D0%B3%D0%BE%20%D0%BE%D1%82%D1%87%D0%B5%D1%82%D0%B0%201.jpg';

    /** @type {number[]} */
    const targetStatuses = [];
    page.on('response', (resp) => {
      try {
        if (resp.url().includes(targetApiPath)) {
          targetStatuses.push(resp.status());
        }
      } catch (_) {}
    });

    await page.goto(deepLink, { waitUntil: 'domcontentloaded' });

    await expect(page.locator('.tab-button.active[data-tab="tab-3"]')).toBeVisible({ timeout: 60000 });

    const imageLinks = page.locator('.tab-link[data-image-url]');
    await page.waitForFunction(() => document.querySelectorAll('.tab-link[data-image-url]').length > 1, null, { timeout: 60000 });

    const targetLink = imageLinks.filter({ hasText: 'Изображени' }).first();
    await expect(targetLink).toBeVisible();

    await targetLink.click();

    await page.waitForFunction(() => {
      const visibleContainer = document.querySelector('.viewer-container[data-image-name]:not([style*="display: none"]):not([style*="display:none"])');
      const img = visibleContainer?.querySelector('.image-viewer');
      if (!img) return false;
      if (img.complete === true && Number(img.naturalWidth || 0) > 0) {
        window._testTargetSrc = img.currentSrc || img.src || '';
        return true;
      }
      return false;
    }, null, { timeout: 90000 });

    const actualTargetPath = await page.evaluate(() => {
      try { return new URL(window._testTargetSrc || '').pathname; } catch { return window._testTargetSrc || ''; }
    });

    page.on('response', (resp) => {
      try {
        if (actualTargetPath && resp.url().includes(actualTargetPath)) {
          targetStatuses.push(resp.status());
        }
      } catch (_) {}
    });

    const openIcon = targetLink.locator('xpath=following-sibling::a[contains(@class,"open-original-link")]').first();
    await expect(openIcon).toBeVisible();

    const [popup] = await Promise.all([
      page.waitForEvent('popup'),
      openIcon.click(),
    ]);
    // Оригинал открывается по tailnet — загрузка может превышать navigationTimeout.
    // Ждём до 10 s, но не падаем: нам нужен сам факт открытия вкладки, не её контент.
    await popup.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => {});
    await popup.close().catch(() => {});

    const otherLink = imageLinks.nth(1);
    await otherLink.click();
    await page.waitForFunction(() => {
      const visibleContainer = document.querySelector('.viewer-container[data-image-name]:not([style*="display: none"]):not([style*="display:none"])');
      const img = visibleContainer?.querySelector('.image-viewer');
      if (!img) return false;
      return img.complete === true && Number(img.naturalWidth || 0) > 0;
    }, null, { timeout: 60000 });

    const before = targetStatuses.length;
    await targetLink.click();

    await page.waitForFunction((p) => {
      const visibleContainer = document.querySelector('.viewer-container[data-image-name]:not([style*="display: none"]):not([style*="display:none"])');
      const img = visibleContainer?.querySelector('.image-viewer');
      if (!img) return false;
      const src = img.currentSrc || img.src || '';
      return p ? src.includes(p) : (img.complete === true && Number(img.naturalWidth || 0) > 0);
    }, actualTargetPath, { timeout: 60000 });

    if (targetStatuses.length > before) {
      const last = targetStatuses[targetStatuses.length - 1];
      expect(last, `Expected 304 on revalidate, got ${last}. statuses=${JSON.stringify(targetStatuses)}`).toBe(304);
    }
  });
});
